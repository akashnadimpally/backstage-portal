import express, { Router } from 'express';
import { execFile, spawn } from 'child_process';
import { readFileSync } from 'fs';
import util from 'util';
import { load as loadYaml } from 'js-yaml';
import type { HttpAuthService, PermissionsService } from '@backstage/backend-plugin-api';
import { AuthorizeResult, createPermission, type BasicPermission } from '@backstage/plugin-permission-common';
import {
  addKubeConfig,
  deleteResource,
  getContexts,
  getCustomResourceInstances,
  getKubeConfig,
  getKubeletMetrics,
  getMetrics,
  getPodLogs,
  getRawMetrics,
  getResources,
  installHelmChart,
  restartDeployment,
  setContext,
} from './kdashm-k8s';

const execFilePromise = util.promisify(execFile);
const subscriptionIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const aksNamePattern = /^[a-z0-9][a-z0-9-]{0,62}$/i;
const resourceGroupPattern = /^[a-z0-9_.()-]{1,90}$/i;
const modelLabels: Record<string, string> = {
  openai_gpt: 'OpenAI GPT',
  claude_ops: 'Claude (Opus)',
  claude_sonnet: 'Claude Sonnet',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
};

const resourcesRead = createPermission({ name: 'kdashm.resources.read', attributes: { action: 'read' } });
const contextManage = createPermission({ name: 'kdashm.context.manage', attributes: { action: 'update' } });
const resourceCreate = createPermission({ name: 'kdashm.resource.create', attributes: { action: 'create' } });
const resourceUpdate = createPermission({ name: 'kdashm.resource.update', attributes: { action: 'update' } });
const resourceDelete = createPermission({ name: 'kdashm.resource.delete', attributes: { action: 'delete' } });
const helmInstall = createPermission({ name: 'kdashm.helm.install', attributes: { action: 'create' } });
const agentUse = createPermission({ name: 'kdashm.agent.use', attributes: { action: 'create' } });

export const kdashmPermissions = [
  resourcesRead,
  contextManage,
  resourceCreate,
  resourceUpdate,
  resourceDelete,
  helmInstall,
  agentUse,
];

function permissionForRequest(req: express.Request): BasicPermission | undefined {
  if (req.method === 'GET') {
    if (req.path === '/agent/models') return agentUse;
    if (['/resources', '/metrics', '/metrics/raw'].includes(req.path) ||
        req.path.startsWith('/metrics/kubelet/') || req.path === '/logs' ||
        req.path === '/contexts' || req.path === '/contexts/aks' || req.path.startsWith('/custom/')) {
      return resourcesRead;
    }
  }

  if (req.method === 'POST') {
    if (req.path === '/agent') return agentUse;
    if (req.path === '/contexts' || req.path === '/contexts/aks/connect') return contextManage;
    if (req.path === '/actions') {
      switch (req.body?.action) {
        case 'applyYaml': return resourceCreate;
        case 'delete': return resourceDelete;
        case 'restart': return resourceUpdate;
        case 'install': return helmInstall;
        default: return undefined;
      }
    }
  }

  return undefined;
}

interface AgentModelConfig {
  API_ENDPOINT: string;
  MODEL_NAME: string;
  API_VERSION?: string;
  ACCESS_KEY: string;
}

function loadAgentModels(): Record<string, AgentModelConfig> {
  try {
    const filePath = process.env.KDASHM_AGENT_MODELS_FILE;
    const config = filePath
      ? readFileSync(filePath, 'utf8')
      : process.env.KDASHM_AGENT_MODELS_JSON || '{}';
    return JSON.parse(config);
  } catch {
    return {};
  }
}

function azureCli() {
  return process.platform === 'win32' ? 'az.cmd' : 'az';
}

export function createKDashMRouter({ httpAuth, permissions }: {
  httpAuth: HttpAuthService;
  permissions: PermissionsService;
}): Router {
  const router = Router();
  router.use(express.json({ limit: '1mb' }));
  router.use((req, res, next) => {
    void (async () => {
      let credentials;
      try {
        credentials = await httpAuth.credentials(req, { allow: ['user'] });
      } catch {
        res.status(401).json({ error: 'Backstage user authentication is required' });
        return;
      }

      const permission = permissionForRequest(req);
      if (!permission) {
        res.status(403).json({ error: 'No permission is defined for this operation' });
        return;
      }

      try {
        const [decision] = await permissions.authorize([{ permission }], { credentials });
        if (decision.result !== AuthorizeResult.ALLOW) {
          res.status(403).json({ error: 'You are not allowed to perform this operation' });
          return;
        }
        next();
      } catch {
        res.status(503).json({ error: 'Permission service is unavailable' });
      }
    })().catch(next);
  });

  router.get('/resources', async (_req, res) => {
    try {
      res.json(await getResources());
    } catch (error) {
      res.status(500).json({ error: error instanceof Error ? error.message : 'Failed to load Kubernetes resources' });
    }
  });

  router.get('/metrics', async (_req, res) => {
    res.json(await getMetrics());
  });

  router.get('/metrics/raw', async (_req, res) => {
    try {
      res.type('text/plain').send(await getRawMetrics());
    } catch {
      res.status(502).json({ error: 'Failed to fetch Prometheus metrics' });
    }
  });

  router.get('/metrics/kubelet/:nodeName', async (req, res) => {
    const nodeName = req.params.nodeName;
    if (!/^[a-z0-9.-]+$/i.test(nodeName)) {
      return res.status(400).json({ error: 'Invalid node name' });
    }
    try {
      res.type('text/plain').send(await getKubeletMetrics(nodeName));
    } catch {
      res.status(502).json({ error: 'Failed to fetch kubelet metrics' });
    }
  });

  router.get('/logs', async (req, res) => {
    const name = String(req.query.name || '');
    const namespace = String(req.query.namespace || '');
    if (!name || !namespace) {
      return res.status(400).json({ error: 'name and namespace are required' });
    }
    res.type('text/plain').send(await getPodLogs(namespace, name));
  });

  router.get('/contexts', (_req, res) => {
    res.json(getContexts());
  });

  router.post('/contexts', (req, res) => {
    const { context, kubeconfig } = req.body ?? {};
    if (typeof kubeconfig === 'string' && kubeconfig.trim()) {
      addKubeConfig(kubeconfig);
      return res.json({ success: true, ...getContexts() });
    }
    if (typeof context === 'string' && getKubeConfig().getContexts().some(item => item.name === context)) {
      setContext(context);
      return res.json({ success: true, ...getContexts() });
    }
    return res.status(400).json({ error: 'A valid context or kubeconfig is required' });
  });

  router.get('/custom/:group/:version/:plural', async (req, res) => {
    const { group, version, plural } = req.params;
    if (!/^[a-z0-9.-]+$/i.test(group) || !/^[a-z0-9]+$/i.test(version) || !/^[a-z0-9-]+$/i.test(plural)) {
      return res.status(400).json({ error: 'Invalid custom resource path' });
    }
    res.json(await getCustomResourceInstances(group, version, plural));
  });

  router.get('/contexts/aks', async (_req, res) => {
    try {
      const accountResult = await execFilePromise(azureCli(), [
        'account', 'list', '--all', '--output', 'json',
      ], { shell: process.platform === 'win32', windowsHide: true });
      const accounts = JSON.parse(accountResult.stdout)
        .filter((account: any) => account.state === 'Enabled' && account.id);
      const clusterLists = await Promise.all(accounts.map(async (account: any) => {
        try {
          const result = await execFilePromise(azureCli(), [
            'aks', 'list', '--subscription', account.id, '--output', 'json',
          ], { shell: process.platform === 'win32', windowsHide: true, maxBuffer: 8 * 1024 * 1024 });
          return JSON.parse(result.stdout).map((cluster: any) => ({
            name: cluster.name,
            title: cluster.name,
            resourceGroup: cluster.resourceGroup,
            subscriptionId: account.id,
            subscriptionName: account.name,
            location: cluster.location,
            provisioningState: cluster.provisioningState,
            kubernetesVersion: cluster.kubernetesVersion,
            id: cluster.id,
          }));
        } catch {
          return [];
        }
      }));
      res.json({ clusters: clusterLists.flat() });
    } catch {
      res.status(401).json({ error: 'Unable to list AKS clusters; sign in with Azure CLI first.' });
    }
  });

  router.post('/contexts/aks/connect', async (req, res) => {
    const { name, resourceGroup, subscriptionId } = req.body ?? {};
    if (
      typeof name !== 'string' || !aksNamePattern.test(name) ||
      typeof resourceGroup !== 'string' || !resourceGroupPattern.test(resourceGroup) ||
      typeof subscriptionId !== 'string' || !subscriptionIdPattern.test(subscriptionId)
    ) {
      return res.status(400).json({ error: 'name, resourceGroup and a valid subscriptionId are required' });
    }
    try {
      await execFilePromise(azureCli(), [
        'aks', 'get-credentials', '--name', name,
        '--resource-group', resourceGroup,
        '--subscription', subscriptionId,
        '--overwrite-existing',
      ], { shell: process.platform === 'win32', windowsHide: true });
      const availableContexts = getContexts();
      const contextName = availableContexts.contexts.find(context => context.name === name)?.name ||
        availableContexts.contexts.find(context => context.name.includes(name))?.name;
      if (contextName) setContext(contextName);
      res.json({ success: true, ...getContexts() });
    } catch {
      res.status(502).json({ error: 'Could not load credentials for this AKS cluster' });
    }
  });

  router.post('/actions', async (req, res) => {
    const { action, kind, name, namespace, strategy, options, chart, releaseName, manifest } = req.body ?? {};

    if (action === 'applyYaml') {
      if (typeof manifest !== 'string' || !manifest.trim() || manifest.length > 1024 * 1024) {
        return res.status(400).json({ error: 'A Kubernetes YAML document up to 1 MB is required' });
      }
      try {
        const parsed = loadYaml(manifest) as any;
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) ||
            typeof parsed.apiVersion !== 'string' || typeof parsed.kind !== 'string' ||
            typeof parsed.metadata?.name !== 'string') {
          return res.status(400).json({ error: 'YAML must describe one resource with apiVersion, kind, and metadata.name' });
        }
        const result = await new Promise<string>((resolve, reject) => {
          const context = getKubeConfig().getCurrentContext();
          const args = [
            ...(context ? ['--context', context] : []),
            'apply', '-f', '-',
          ];
          const child = spawn('kubectl', args, { windowsHide: true });
          let stdout = '';
          let stderr = '';
          child.stdout?.on('data', chunk => { stdout += chunk.toString(); });
          child.stderr?.on('data', chunk => { stderr += chunk.toString(); });
          child.once('error', reject);
          child.once('close', code => {
            if (code === 0) resolve(stdout.trim());
            else reject(new Error(stderr.trim() || `kubectl exited with code ${code}`));
          });
          child.stdin?.end(manifest);
        });
        return res.json({ success: true, result });
      } catch (error) {
        return res.status(400).json({ error: error instanceof Error ? error.message : 'Failed to apply YAML' });
      }
    }

    if (typeof kind !== 'string' || typeof name !== 'string') {
      return res.status(400).json({ error: 'kind and name are required' });
    }
    try {
      if (action === 'delete') {
        await deleteResource(kind, name, namespace);
        return res.json({ success: true });
      }
      if (action === 'restart' && kind.toLowerCase() === 'deployment') {
        await restartDeployment(name, namespace, strategy, options);
        return res.json({ success: true });
      }
      if (action === 'install' && kind === 'helmChart') {
        if (typeof chart !== 'string' || typeof releaseName !== 'string' || typeof namespace !== 'string') {
          return res.status(400).json({ error: 'chart, releaseName and namespace are required' });
        }
        await installHelmChart(chart, releaseName, namespace);
        return res.json({ success: true });
      }
      return res.status(400).json({ error: 'Unsupported Kubernetes action' });
    } catch (error) {
      res.status(500).json({ error: error instanceof Error ? error.message : 'Kubernetes action failed' });
    }
  });

  router.get('/agent/models', (_req, res) => {
    const configuredModels = loadAgentModels();
    const ids = new Set([...Object.keys(modelLabels), ...Object.keys(configuredModels)]);
    res.json({
      models: [...ids].map(id => {
        const model = configuredModels[id];
        return {
          id,
          label: modelLabels[id] || id,
          configured: Boolean(model?.API_ENDPOINT && model?.MODEL_NAME && model?.ACCESS_KEY),
        };
      }),
    });
  });

  router.post('/agent', async (req, res) => {
    const configuredModels = loadAgentModels();
    const { model = 'openai_gpt', messages = [], clusterSummary = {} } = req.body ?? {};
    const modelConfig = configuredModels[model] as AgentModelConfig | undefined;
    if (!modelConfig?.API_ENDPOINT || !modelConfig.MODEL_NAME || !modelConfig.ACCESS_KEY) {
      return res.status(503).json({ error: `Model "${model}" is not configured in KDASHM_AGENT_MODELS_JSON.` });
    }
    if (!Array.isArray(messages)) {
      return res.status(400).json({ error: 'messages must be an array' });
    }

    let resources: any = {};
    try {
      resources = await getResources();
    } catch {
      // Keep the SRE agent available with the context supplied by the dashboard.
    }
    const pods = resources.pods || [];
    const nodes = resources.nodes || [];
    const events = (resources.events || []).filter((event: any) => event.type === 'Warning').slice(0, 10);
    const systemPrompt = [
      'You are an expert Kubernetes site reliability engineer. Use the supplied cluster data and provide concise, actionable analysis.',
      `Cluster context: ${resources.currentContext || clusterSummary.contextName || 'unknown'}`,
      `Namespace filter: ${clusterSummary.namespace || 'all'}`,
      `Pods: ${pods.length}; running: ${pods.filter((pod: any) => pod.status?.phase === 'Running').length}; pending: ${pods.filter((pod: any) => pod.status?.phase === 'Pending').length}; failed: ${pods.filter((pod: any) => pod.status?.phase === 'Failed').length}.`,
      `Nodes: ${nodes.length}; ready: ${nodes.filter((node: any) => node.status?.conditions?.some((condition: any) => condition.type === 'Ready' && condition.status === 'True')).length}.`,
      `Recent warning events: ${events.map((event: any) => `${event.involvedObject?.kind}/${event.involvedObject?.name}: ${event.reason} - ${event.message}`).join('\n') || 'none'}`,
      'When suggesting changes, show explicit commands or YAML and call out destructive actions.',
    ].join('\n');

    const endpoint = modelConfig.API_ENDPOINT.replace(/\/$/, '');
    const apiVersion = modelConfig.API_VERSION || '2024-02-01';
    const url = `${endpoint}/openai/deployments/${encodeURIComponent(modelConfig.MODEL_NAME)}/chat/completions?api-version=${encodeURIComponent(apiVersion)}`;
    let upstream: Response;
    try {
      upstream = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'api-key': modelConfig.ACCESS_KEY,
        },
        body: JSON.stringify({
          messages: [
            { role: 'system', content: systemPrompt },
            ...messages.slice(-20).map((message: any) => ({
              role: message.role === 'assistant' ? 'assistant' : 'user',
              content: String(message.content || ''),
            })),
          ],
          stream: true,
          max_tokens: 2048,
          temperature: 0.3,
        }),
        signal: AbortSignal.timeout(120_000),
      });
    } catch {
      return res.status(502).json({ error: 'The configured model endpoint could not be reached.' });
    }

    if (!upstream.ok || !upstream.body) {
      return res.status(502).json({ error: `The configured model endpoint returned HTTP ${upstream.status}.` });
    }

    res.status(200).set({
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    });
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        buffered += decoder.decode(value, { stream: !done });
        const lines = buffered.split(/\r?\n/);
        buffered = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (payload === '[DONE]') {
            res.end();
            return;
          }
          try {
            const delta = JSON.parse(payload).choices?.[0]?.delta?.content;
            if (delta) res.write(`data: ${JSON.stringify({ content: delta })}\n\n`);
          } catch {
            // Ignore malformed upstream SSE frames.
          }
        }
        if (done) break;
      }
      res.end();
    } catch {
      if (!res.headersSent) res.status(502);
      res.end();
    } finally {
      reader.releaseLock();
    }
  });

  return router;
}