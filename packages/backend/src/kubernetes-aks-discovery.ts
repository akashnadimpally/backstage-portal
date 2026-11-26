import { execFile } from 'child_process';
import util from 'util';
import { coreServices, createBackendModule } from '@backstage/backend-plugin-api';
import {
  kubernetesClusterSupplierExtensionPoint,
  type ClusterDetails,
  type KubernetesClustersSupplier,
} from '@backstage/plugin-kubernetes-node';

const execFilePromise = util.promisify(execFile);
const cacheDurationMs = 60_000;
let cachedClusters: ClusterDetails[] = [];
let cacheExpiresAt = 0;
let pendingDiscovery: Promise<ClusterDetails[]> | undefined;

function azureCliCommand() {
  return process.platform === 'win32' ? 'az.cmd' : 'az';
}

async function discoverAksClusters(logger: Pick<Console, 'warn'>): Promise<ClusterDetails[]> {
  if (Date.now() < cacheExpiresAt) return cachedClusters;
  if (pendingDiscovery) return pendingDiscovery;

  pendingDiscovery = (async () => {
    const accountsResult = await execFilePromise(
      azureCliCommand(),
      ['account', 'list', '--all', '--output', 'json'],
      { shell: process.platform === 'win32', windowsHide: true },
    );
    const subscriptions = JSON.parse(accountsResult.stdout)
      .filter((account: any) => account.state === 'Enabled' && account.id);

    const clusterGroups = await Promise.all(subscriptions.map(async (account: any) => {
      try {
        const result = await execFilePromise(
          azureCliCommand(),
          ['aks', 'list', '--subscription', account.id, '--output', 'json'],
          { shell: process.platform === 'win32', windowsHide: true, maxBuffer: 8 * 1024 * 1024 },
        );
        return JSON.parse(result.stdout).map((cluster: any): ClusterDetails | undefined => {
          const endpoint = cluster.fqdn || cluster.privateFqdn;
          if (!endpoint) return undefined;
          return {
            name: `aks-${account.id.slice(0, 8)}-${cluster.name}`,
            title: `${cluster.name} (${account.name})`,
            url: `https://${endpoint}`,
            authMetadata: { 'kubernetes.io/auth-provider': 'azure' },
            dashboardApp: 'aks',
            skipTLSVerify: false,
            skipMetricsLookup: false,
          };
        }).filter((cluster: ClusterDetails | undefined): cluster is ClusterDetails => Boolean(cluster));
      } catch (error) {
        logger.warn(`AKS discovery skipped subscription ${account.id}: ${String(error)}`);
        return [];
      }
    }));

    cachedClusters = clusterGroups.flat();
    cacheExpiresAt = Date.now() + cacheDurationMs;
    return cachedClusters;
  })().finally(() => {
    pendingDiscovery = undefined;
  });

  return pendingDiscovery;
}

export const kubernetesAksDiscoveryModule = createBackendModule({
  pluginId: 'kubernetes',
  moduleId: 'azure-aks-discovery',
  register(env) {
    env.registerInit({
      deps: {
        clusterSupplier: kubernetesClusterSupplierExtensionPoint,
        logger: coreServices.logger,
      },
      async init({ clusterSupplier, logger }) {
        clusterSupplier.addClusterSupplier(async ({ getDefault }) => {
          const localSupplier = await getDefault();
          return {
            async getClusters(options) {
              const localClusters = await localSupplier.getClusters(options);
              let aksClusters: ClusterDetails[] = [];
              try {
                aksClusters = await discoverAksClusters(logger);
              } catch (error) {
                logger.warn(`AKS discovery is unavailable; keeping local clusters: ${String(error)}`);
              }
              const knownNames = new Set(localClusters.map(cluster => cluster.name));
              return [
                ...localClusters,
                ...aksClusters.filter(cluster => !knownNames.has(cluster.name)),
              ];
            },
          } satisfies KubernetesClustersSupplier;
        });
      },
    });
  },
});

export default kubernetesAksDiscoveryModule;