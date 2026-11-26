import * as k8s from '@kubernetes/client-node';
import { execFile } from 'child_process';
import util from 'util';

const execFilePromise = util.promisify(execFile);

let kc = new k8s.KubeConfig();
let customConfigs: string[] = [];

function runKubectl(args: string[], maxBuffer?: number) {
    const context = kc.getCurrentContext();
    return execFilePromise('kubectl', [
        ...(context ? ['--context', context] : []),
        ...args,
    ], { windowsHide: true, ...(maxBuffer ? { maxBuffer } : {}) });
}

function runHelm(args: string[]) {
    const context = kc.getCurrentContext();
    return execFilePromise('helm', [
        ...(context ? ['--kube-context', context] : []),
        ...args,
    ], { windowsHide: true });
}

try {
    kc.loadFromDefault();
} catch (e) {
    console.warn('Failed to load default kubeconfig at startup:', e);
}

function refreshKubeConfig() {
    const newKc = new k8s.KubeConfig();
    try {
        newKc.loadFromDefault();
    } catch (e) {
        console.warn('Could not reload default kubeconfig:', e);
    }

    for (const configStr of customConfigs) {
        try {
            const tempKc = new k8s.KubeConfig();
            tempKc.loadFromString(configStr);
            newKc.clusters.push(...tempKc.clusters);
            newKc.users.push(...tempKc.users);
            newKc.contexts.push(...tempKc.contexts);
        } catch (e) {
            console.error('Error applying custom kubeconfig:', e);
        }
    }

    const oldContext = kc.getCurrentContext();
    kc = newKc;

    if (oldContext && kc.getContexts().some(c => c.name === oldContext)) {
        kc.setCurrentContext(oldContext);
    } else if (kc.getContexts().length > 0) {
        const defaultContext = kc.getContexts().find(c => 
            c.name.includes('docker-desktop') || 
            c.name.includes('docker-for-desktop') || 
            c.name === 'minikube'
        );
        if (defaultContext) {
            kc.setCurrentContext(defaultContext.name);
        } else {
            kc.setCurrentContext(kc.getContexts()[0].name);
        }
    }
}

export function getKubeConfig() {
    return kc;
}

export function setContext(contextName: string) {
    kc.setCurrentContext(contextName);
}

export function addKubeConfig(configStr: string) {
    customConfigs.push(configStr);
    refreshKubeConfig();
}

export function getContexts() {
    refreshKubeConfig();
    return {
        contexts: kc.getContexts(),
        currentContext: kc.getCurrentContext(),
    };
}

export const coreApi = () => kc.makeApiClient(k8s.CoreV1Api);
export const appsApi = () => kc.makeApiClient(k8s.AppsV1Api);
export const storageApi = () => kc.makeApiClient(k8s.StorageV1Api);
export const networkingApi = () => kc.makeApiClient(k8s.NetworkingV1Api);
export const apiExtensionsApi = () => kc.makeApiClient(k8s.ApiextensionsV1Api);
export const customObjectsApi = () => kc.makeApiClient(k8s.CustomObjectsApi);
export const batchApi = () => kc.makeApiClient(k8s.BatchV1Api);
export const rbacApi = () => kc.makeApiClient(k8s.RbacAuthorizationV1Api);
export const autoscalingApi = () => kc.makeApiClient(k8s.AutoscalingV2Api);

async function safeList(promise: Promise<any>) {
    try {
        const res = await promise;
        return res.items || res.body?.items || [];
    } catch (e) {
        console.error('Failed to list resource:', e);
        return [];
    }
}

export async function getResources() {
    const [
        pods,
        namespaces,
        deployments,
        statefulSets,
        configMaps,
        secrets,
        services,
        nodes,
        pvcs,
        pvs,
        storageClasses,
        networkPolicies,
        crds,
        ingresses,
        jobs,
        cronJobs,
        roles,
        clusterRoles,
        roleBindings,
        clusterRoleBindings,
        serviceAccounts,
        events,
        helmReleases,
        helmCharts,
        hpas,
        replicaSets,
        daemonSets
    ] = await Promise.all([
        safeList(coreApi().listPodForAllNamespaces()),
        safeList(coreApi().listNamespace()),
        safeList(appsApi().listDeploymentForAllNamespaces()),
        safeList(appsApi().listStatefulSetForAllNamespaces()),
        safeList(coreApi().listConfigMapForAllNamespaces()),
        safeList(coreApi().listSecretForAllNamespaces()),
        safeList(coreApi().listServiceForAllNamespaces()),
        safeList(coreApi().listNode()),
        safeList(coreApi().listPersistentVolumeClaimForAllNamespaces()),
        safeList(coreApi().listPersistentVolume()),
        safeList(storageApi().listStorageClass()),
        safeList(networkingApi().listNetworkPolicyForAllNamespaces()),
        safeList(apiExtensionsApi().listCustomResourceDefinition()),
        safeList(networkingApi().listIngressForAllNamespaces()),
        safeList(batchApi().listJobForAllNamespaces()),
        safeList(batchApi().listCronJobForAllNamespaces()),
        safeList(rbacApi().listRoleForAllNamespaces()),
        safeList(rbacApi().listClusterRole()),
        safeList(rbacApi().listRoleBindingForAllNamespaces()),
        safeList(rbacApi().listClusterRoleBinding()),
        safeList(coreApi().listServiceAccountForAllNamespaces()),
        safeList(coreApi().listEventForAllNamespaces()),
        getHelmReleases(),
        getHelmCharts(),
        safeList(autoscalingApi().listHorizontalPodAutoscalerForAllNamespaces()),
        safeList(appsApi().listReplicaSetForAllNamespaces()),
        safeList(appsApi().listDaemonSetForAllNamespaces()),
    ]);

    return {
        pods,
        namespaces,
        deployments,
        statefulSets,
        configMaps,
        secrets,
        services,
        nodes,
        pvcs,
        pvs,
        storageClasses,
        networkPolicies,
        crds,
        ingresses,
        jobs,
        cronJobs,
        roles,
        clusterRoles,
        roleBindings,
        clusterRoleBindings,
        serviceAccounts,
        events,
        helmReleases,
        helmCharts,
        hpas,
        replicaSets,
        daemonSets,
        currentContext: kc.getCurrentContext()
    };
}

export async function getPodLogs(namespace: string, name: string) {
    try {
        const res = await coreApi().readNamespacedPodLog({ name, namespace });
        return typeof res === 'string' ? res : (res as any).body;
    } catch (e) {
        return `Error fetching logs: ${e}`;
    }
}

export async function deleteResource(kind: string, name: string, namespace?: string) {
    const k = kind.toLowerCase();

    if (k === 'pod' && namespace) return coreApi().deleteNamespacedPod({ name, namespace });
    if (k === 'deployment' && namespace) return appsApi().deleteNamespacedDeployment({ name, namespace });
    if (k === 'service' && namespace) return coreApi().deleteNamespacedService({ name, namespace });
    if (k === 'statefulset' && namespace) return appsApi().deleteNamespacedStatefulSet({ name, namespace });
    if (k === 'configmap' && namespace) return coreApi().deleteNamespacedConfigMap({ name, namespace });
    if (k === 'secret' && namespace) return coreApi().deleteNamespacedSecret({ name, namespace });
    if (k === 'namespace') return coreApi().deleteNamespace({ name });

    if (k === 'helmrelease' && namespace) {
        const { stdout } = await runHelm(['uninstall', name, '--namespace', namespace]);
        return stdout;
    }

    throw new Error(`Deletion for ${kind} not implemented or namespace missing`);
}

export async function restartDeployment(name: string, namespace: string, strategy?: string, options?: any) {
    const restartedAt = new Date().toISOString();
    console.log(`[Restart] Targeting ${namespace}/${name} with strategy: ${strategy || 'default'}`);

    // Base patch structure
    const patch: any = {
        spec: {
            template: {
                metadata: {
                    annotations: {
                        'kubectl.kubernetes.io/restartedAt': restartedAt
                    }
                }
            }
        }
    };

    // Only add strategy if specifically requested
    if (strategy) {
        patch.spec.strategy = {
            type: (strategy === 'canary' || strategy === 'RollingUpdate') ? 'RollingUpdate' : 'Recreate'
        };

        if (patch.spec.strategy.type === 'RollingUpdate') {
            patch.spec.strategy.rollingUpdate = {
                maxSurge: strategy === 'canary' ? 1 : (options?.maxSurge || '25%'),
                maxUnavailable: strategy === 'canary' ? 0 : (options?.maxUnavailable || '25%')
            };
        }
    }

    const api = appsApi();

    try {
        console.log('[Restart] Sending Strategic Merge Patch');
        await (api as any).patchNamespacedDeployment(
            { name, namespace, body: patch },
            { headers: { 'Content-Type': 'application/strategic-merge-patch+json' } }
        );
        console.log(`[Success] Restarted ${name}`);
        return { success: true };
    } catch (e: any) {
        console.warn(`[Restart] Strategic patch failed, trying JSON patch:`, e.body || e.message);

        const jsonPatch = [
            {
                op: 'add',
                path: '/spec/template/metadata/annotations/kubectl.kubernetes.io~1restartedAt',
                value: restartedAt
            }
        ];

        try {
            await (api as any).patchNamespacedDeployment(
                { name, namespace, body: jsonPatch },
                { headers: { 'Content-Type': 'application/json-patch+json' } }
            );
            console.log(`[Success] Restarted ${name} via Fallback`);
            return { success: true };
        } catch (err: any) {
            console.error('[Restart] All attempts failed');
            throw err;
        }
    }
}


export async function getMetrics() {
    try {
        const res: any = await customObjectsApi().listClusterCustomObject({ group: 'metrics.k8s.io', version: 'v1beta1', plural: 'pods' });
        const nodeRes: any = await customObjectsApi().listClusterCustomObject({ group: 'metrics.k8s.io', version: 'v1beta1', plural: 'nodes' });
        return {
            pods: res.body?.items || res.items || [],
            nodes: nodeRes.body?.items || nodeRes.items || []
        };
    } catch (e) {
        console.error('Metrics fetch error:', e);
        return { pods: [], nodes: [], error: 'Metrics Server not found' };
    }
}

export async function getRawMetrics() {
    const { stdout } = await runKubectl(['get', '--raw', '/metrics'], 10 * 1024 * 1024);
    return stdout;
}

export async function getKubeletMetrics(nodeName: string) {
    try {
        const { stdout } = await runKubectl([
            'get', '--raw', `/api/v1/nodes/${encodeURIComponent(nodeName)}/proxy/metrics`,
        ], 10 * 1024 * 1024);
        return stdout;
    } catch (error) {
        console.warn(`[Metrics] Kubelet fetch failed for ${nodeName}:`, error);
        return '';
    }
}
export async function getHelmReleases() {
    try {
        const { stdout } = await runHelm(['list', '--all-namespaces', '--output', 'json']);
        return JSON.parse(stdout);
    } catch (error) {
        console.error('[Helm] Failed to list releases:', error);
        return [];
    }
}
export async function getHelmCharts() {
    try {
        const { stdout } = await runHelm(['search', 'repo', '--output', 'json']);
        return JSON.parse(stdout);
    } catch (error) {
        console.warn('[Helm] Failed to search charts:', error);
        return [];
    }
}
export async function installHelmChart(chartName: string, releaseName: string, namespace: string = 'default') {
    const { stdout } = await runHelm([
        'install', releaseName, chartName,
        '--namespace', namespace,
        '--create-namespace',
    ]);
    return stdout;
}

export async function getCustomResourceInstances(group: string, version: string, plural: string) {
    try {
        const res: any = await customObjectsApi().listClusterCustomObject({ group, version, plural });
        return res.body?.items || res.items || [];
    } catch (e: any) {
        // Some CRDs are namespace-scoped; try all namespaces fallback
        try {
            const res: any = await customObjectsApi().listCustomObjectForAllNamespaces({ group, version, plural });
            return res.body?.items || res.items || [];
        } catch {
            console.error(`[CRD] Failed to list ${group}/${version}/${plural}:`, e.message || e);
            return [];
        }
    }
}
