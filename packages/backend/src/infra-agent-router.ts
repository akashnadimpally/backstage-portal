import express, { Router } from 'express';
import { createPermission, type BasicPermission } from '@backstage/plugin-permission-common';
import type { HttpAuthService, PermissionsService } from '@backstage/backend-plugin-api';
import { execFile } from 'child_process';
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';
import os from 'os';

export const infraAgentReadPermission = createPermission({
  name: 'infraagent.read',
  attributes: { action: 'read' },
});
export const infraAgentRunPermission = createPermission({
  name: 'infraagent.run',
  attributes: { action: 'create' },
});
export const infraAgentApprovePermission = createPermission({
  name: 'infraagent.approve',
  attributes: { action: 'update' },
});
export const infraAgentApplyPermission = createPermission({
  name: 'infraagent.apply',
  attributes: { action: 'update' },
});

export const infraAgentPermissions = [
  infraAgentReadPermission,
  infraAgentRunPermission,
  infraAgentApprovePermission,
  infraAgentApplyPermission,
];

class InfraAgentError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function findInfraAgentDir(): string {
  if (process.env.INFRA_AGENT_DIR && fs.existsSync(process.env.INFRA_AGENT_DIR)) {
    return path.resolve(process.env.INFRA_AGENT_DIR);
  }
  const candidates = [
    path.resolve(__dirname, '../../../infra-agent'),
    path.resolve(process.cwd(), 'infra-agent'),
    path.resolve(process.cwd(), '../infra-agent'),
    path.resolve(__dirname, '../../infra-agent'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.existsSync(path.join(candidate, 'config.yaml'))) {
      return candidate;
    }
  }
  return path.resolve(process.cwd(), 'infra-agent');
}

function findPythonBin(infraAgentDir: string): string {
  if (process.env.PYTHON_BIN && fs.existsSync(process.env.PYTHON_BIN)) {
    return process.env.PYTHON_BIN;
  }
  const venvBin =
    process.platform === 'win32'
      ? path.join(infraAgentDir, '.venv', 'Scripts', 'python.exe')
      : path.join(infraAgentDir, '.venv', 'bin', 'python');
  if (fs.existsSync(venvBin)) {
    return venvBin;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function runInfraAgent(
  args: string[],
  infraAgentDir: string,
  timeoutMs = 600000,
): Promise<{ stdout: string; stderr: string; code: number }> {
  const pythonBin = findPythonBin(infraAgentDir);
  const configPath = path.join(infraAgentDir, 'config.yaml');

  return new Promise((resolve, reject) => {
    execFile(
      pythonBin,
      ['-m', 'agent.main', ...args],
      {
        cwd: infraAgentDir,
        windowsHide: true,
        timeout: timeoutMs,
        maxBuffer: 25 * 1024 * 1024,
        env: {
          ...process.env,
          PYTHONUNBUFFERED: '1',
          INFRA_AGENT_CONFIG: configPath,
        },
      },
      (error, stdout, stderr) => {
        const outStr = stdout?.toString() || '';
        const errStr = stderr?.toString() || '';
        if (error && (error as any).killed) {
          reject(new InfraAgentError(504, `Command timed out after ${timeoutMs}ms`));
          return;
        }
        resolve({
          stdout: outStr,
          stderr: errStr,
          code: error ? (typeof error.code === 'number' ? error.code : 1) : 0,
        });
      },
    );
  });
}

function requestPermission(req: express.Request): BasicPermission | undefined {
  if (req.method === 'GET') return infraAgentReadPermission;
  if (req.method === 'POST') {
    if (req.path === '/validate' || req.path === '/requests/run') return infraAgentRunPermission;
    if (req.path.endsWith('/approve')) return infraAgentApprovePermission;
    if (req.path.endsWith('/apply')) return infraAgentApplyPermission;
  }
  return undefined;
}

function access(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction,
  httpAuth: HttpAuthService,
  permissions: PermissionsService,
) {
  void (async () => {
    let credentials;
    try {
      credentials = await httpAuth.credentials(req, { allow: ['user'] });
    } catch {
      res.status(401).json({ error: 'Backstage user authentication is required' });
      return;
    }
    const permission = requestPermission(req);
    if (!permission) {
      next();
      return;
    }
    try {
      const decision = await permissions.authorize([{ permission }], { credentials });
      if (decision[0].result !== 'ALLOW') {
        res.status(403).json({ error: `Permission denied for ${permission.name}` });
        return;
      }
      next();
    } catch (e) {
      // In development or if permission check fails, proceed if authorized
      next();
    }
  })();
}

function sanitizeId(id: string): string {
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
    throw new InfraAgentError(400, 'Invalid request ID format');
  }
  return id;
}

export function createInfraAgentRouter({
  httpAuth,
  permissions,
}: {
  httpAuth: HttpAuthService;
  permissions: PermissionsService;
}): Router {
  const router = Router();
  router.use(express.json({ limit: '10mb' }));
  router.use((req, res, next) => access(req, res, next, httpAuth, permissions));

  const infraAgentDir = findInfraAgentDir();

  // GET /status — setup & configuration status
  router.get('/status', (_req, res) => {
    try {
      const pythonBin = findPythonBin(infraAgentDir);
      const configPath = path.join(infraAgentDir, 'config.yaml');
      const catalogPath = path.join(infraAgentDir, 'module-catalog', 'catalog.yaml');

      let config: any = null;
      if (fs.existsSync(configPath)) {
        config = yaml.load(fs.readFileSync(configPath, 'utf8'));
      }

      let moduleCount = 0;
      if (fs.existsSync(catalogPath)) {
        const cat = yaml.load(fs.readFileSync(catalogPath, 'utf8')) as any;
        if (cat && cat.modules) {
          moduleCount = Object.keys(cat.modules).length;
        }
      }

      const activeProvider = config?.ollama?.enabled ? 'ollama' : (config?.azure_foundry?.enabled ? 'azure_foundry' : 'none');
      const providerConfig = activeProvider === 'ollama' ? config?.ollama : config?.azure_foundry;
      const isPlaceholder = activeProvider === 'ollama'
        ? (providerConfig?.base_url?.includes('<OLLAMA_') || providerConfig?.base_url?.includes('PLACEHOLDER'))
        : (providerConfig?.endpoint?.includes('PLACEHOLDER'));

      res.json({
        available: fs.existsSync(infraAgentDir),
        infraAgentDir,
        pythonBin,
        hasVenv: fs.existsSync(path.join(infraAgentDir, '.venv')),
        activeProvider,
        isPlaceholder,
        moduleCount,
        policy: config?.policy,
        models: providerConfig?.models || {},
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /check-llm — verify LLM connectivity and models
  router.get('/check-llm', async (_req, res) => {
    try {
      const { stdout, stderr, code } = await runInfraAgent(['check-llm'], infraAgentDir, 30000);
      try {
        const parsed = JSON.parse(stdout);
        res.json({ ok: code === 0, ...parsed });
      } catch {
        res.json({ ok: code === 0, output: stdout, error: stderr });
      }
    } catch (err: any) {
      res.status(502).json({ error: err.message });
    }
  });

  // GET /catalog — full catalog of approved Terraform modules
  router.get('/catalog', (_req, res) => {
    try {
      const catalogPath = path.join(infraAgentDir, 'module-catalog', 'catalog.yaml');
      if (!fs.existsSync(catalogPath)) {
        res.status(404).json({ error: 'Catalog file not found' });
        return;
      }
      const data = yaml.load(fs.readFileSync(catalogPath, 'utf8')) as any;
      res.json({
        modules: data?.modules || {},
        root: data?.root || '',
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /catalog/:module — specific module details
  router.get('/catalog/:module', (req, res) => {
    try {
      const moduleName = req.params.module;
      const catalogPath = path.join(infraAgentDir, 'module-catalog', 'catalog.yaml');
      if (!fs.existsSync(catalogPath)) {
        res.status(404).json({ error: 'Catalog file not found' });
        return;
      }
      const data = yaml.load(fs.readFileSync(catalogPath, 'utf8')) as any;
      const mod = data?.modules?.[moduleName];
      if (!mod) {
        res.status(404).json({ error: `Module ${moduleName} not found in catalog` });
        return;
      }
      res.json({ module: mod });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /requests — list of all infrastructure requests
  router.get('/requests', (_req, res) => {
    try {
      const requestsDir = path.join(infraAgentDir, 'generated', 'requests');
      if (!fs.existsSync(requestsDir)) {
        res.json({ requests: [] });
        return;
      }
      const entries = fs.readdirSync(requestsDir, { withFileTypes: true });
      const requests = entries
        .filter(e => e.isDirectory() && !e.name.startsWith('.'))
        .map(e => {
          const id = e.name;
          const reqDir = path.join(requestsDir, id);
          let state: any = null;
          let request: any = null;
          let planSummary: any = null;
          let result: any = null;

          try {
            if (fs.existsSync(path.join(reqDir, 'state.json'))) {
              state = JSON.parse(fs.readFileSync(path.join(reqDir, 'state.json'), 'utf8'));
            }
            if (fs.existsSync(path.join(reqDir, 'request.json'))) {
              request = JSON.parse(fs.readFileSync(path.join(reqDir, 'request.json'), 'utf8'));
            }
            if (fs.existsSync(path.join(reqDir, 'plan-summary.json'))) {
              planSummary = JSON.parse(fs.readFileSync(path.join(reqDir, 'plan-summary.json'), 'utf8'));
            }
            if (fs.existsSync(path.join(reqDir, 'result.json'))) {
              result = JSON.parse(fs.readFileSync(path.join(reqDir, 'result.json'), 'utf8'));
            }
          } catch {
            // Ignore parse errors on individual files
          }

          return {
            id,
            status: state?.status || 'UNKNOWN',
            statusMessage: state?.status_message || '',
            description: request?.description || '',
            environment: request?.environment || '',
            location: request?.location || '',
            requestedBy: request?.requested_by || '',
            planSummary,
            result,
            createdAt: state?.created_at || fs.statSync(reqDir).birthtime.toISOString(),
          };
        })
        .sort((a, b) => b.id.localeCompare(a.id));

      res.json({ requests });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /requests/:id — full details of a specific request
  router.get('/requests/:id', (req, res) => {
    try {
      const id = sanitizeId(req.params.id);
      const reqDir = path.join(infraAgentDir, 'generated', 'requests', id);
      if (!fs.existsSync(reqDir)) {
        res.status(404).json({ error: `Request ${id} not found` });
        return;
      }

      const readJson = (filename: string) => {
        const filePath = path.join(reqDir, filename);
        if (fs.existsSync(filePath)) {
          try {
            return JSON.parse(fs.readFileSync(filePath, 'utf8'));
          } catch {
            return null;
          }
        }
        return null;
      };

      const readText = (filename: string) => {
        const filePath = path.join(reqDir, filename);
        if (fs.existsSync(filePath)) {
          try {
            return fs.readFileSync(filePath, 'utf8');
          } catch {
            return null;
          }
        }
        return null;
      };

      // Collect .tf and tfvars files
      const tfFiles: Record<string, string> = {};
      const knownFiles = ['main.tf', 'providers.tf', 'variables.tf', 'outputs.tf', 'terraform.tfvars.json'];
      for (const f of knownFiles) {
        const content = readText(f);
        if (content !== null) tfFiles[f] = content;
      }

      // Collect audit logs
      let auditLogs: any[] = [];
      const auditText = readText('audit.jsonl');
      if (auditText) {
        auditLogs = auditText
          .split('\n')
          .map(line => line.trim())
          .filter(Boolean)
          .map(line => {
            try {
              return JSON.parse(line);
            } catch {
              return { raw: line };
            }
          });
      }

      // Collect list of logs
      const logsDir = path.join(reqDir, 'logs');
      let availableLogs: string[] = [];
      if (fs.existsSync(logsDir)) {
        availableLogs = fs.readdirSync(logsDir).filter(f => !f.startsWith('.'));
      }

      res.json({
        id,
        dir: reqDir,
        state: readJson('state.json'),
        request: readJson('request.json'),
        requirements: readJson('requirements.json'),
        generatedConfig: readJson('generated-config.json'),
        review: readJson('review.json'),
        planSummary: readJson('plan-summary.json'),
        plan: readJson('plan.json'),
        result: readJson('result.json'),
        tfFiles,
        auditLogs,
        availableLogs,
      });
    } catch (err: any) {
      res.status(err instanceof InfraAgentError ? err.status : 500).json({ error: err.message });
    }
  });

  // GET /requests/:id/logs/:name — log file content
  router.get('/requests/:id/logs/:name', (req, res) => {
    try {
      const id = sanitizeId(req.params.id);
      const logName = path.basename(req.params.name);
      const logPath = path.join(infraAgentDir, 'generated', 'requests', id, 'logs', logName);
      if (!fs.existsSync(logPath)) {
        res.status(404).json({ error: `Log ${logName} not found` });
        return;
      }
      res.type('text/plain').send(fs.readFileSync(logPath, 'utf8'));
    } catch (err: any) {
      res.status(err instanceof InfraAgentError ? err.status : 500).json({ error: err.message });
    }
  });

  // POST /validate — validate request YAML / JSON before submitting
  router.post('/validate', async (req, res) => {
    let tempFile = '';
    try {
      const body = req.body;
      const content = body.content || (typeof body === 'string' ? body : yaml.dump(body));
      tempFile = path.join(os.tmpdir(), `req-val-${Date.now()}-${Math.random().toString(36).slice(2)}.yaml`);
      fs.writeFileSync(tempFile, content, 'utf8');

      const { stdout, stderr, code } = await runInfraAgent(['validate-request', '-f', tempFile], infraAgentDir, 15000);
      if (code === 0) {
        let requestObj: any = null;
        try {
          const lines = stdout.split('\n');
          const jsonStartIndex = lines.findIndex(l => l.trim().startsWith('{'));
          if (jsonStartIndex !== -1) {
            requestObj = JSON.parse(lines.slice(jsonStartIndex).join('\n'));
          }
        } catch {
          // ignore
        }
        res.json({ valid: true, request: requestObj, message: stdout });
      } else {
        res.status(400).json({ valid: false, error: stderr || stdout });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    } finally {
      if (tempFile && fs.existsSync(tempFile)) {
        try { fs.unlinkSync(tempFile); } catch {}
      }
    }
  });

  // POST /requests/run — trigger the pipeline
  router.post('/requests/run', async (req, res) => {
    let tempFile = '';
    try {
      const body = req.body;
      const content = body.content || (typeof body === 'string' ? body : yaml.dump(body));
      tempFile = path.join(os.tmpdir(), `req-run-${Date.now()}-${Math.random().toString(36).slice(2)}.yaml`);
      fs.writeFileSync(tempFile, content, 'utf8');

      const { stdout, stderr, code } = await runInfraAgent(['run', '-f', tempFile, '--json'], infraAgentDir, 900000);

      try {
        const result = JSON.parse(stdout.trim());
        res.json({ ok: code === 0, ...result });
      } catch {
        // Fallback if not pure JSON
        res.json({
          ok: code === 0,
          output: stdout,
          error: stderr || undefined,
        });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    } finally {
      if (tempFile && fs.existsSync(tempFile)) {
        try { fs.unlinkSync(tempFile); } catch {}
      }
    }
  });

  // POST /requests/:id/approve — issue single-use apply token
  router.post('/requests/:id/approve', async (req, res) => {
    try {
      const id = sanitizeId(req.params.id);
      const approver = req.body.approver?.trim() || 'Portal User';

      const { stdout, stderr, code } = await runInfraAgent(
        ['approve', id, '--approver', approver, '--yes', '--json'],
        infraAgentDir,
        30000,
      );

      if (code !== 0) {
        res.status(400).json({ error: stderr || stdout || 'Approval failed' });
        return;
      }

      try {
        const result = JSON.parse(stdout.trim());
        res.json(result);
      } catch {
        res.json({ request_id: id, approver, output: stdout });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /requests/:id/apply — apply the approved plan
  router.post('/requests/:id/apply', async (req, res) => {
    try {
      const id = sanitizeId(req.params.id);
      const token = req.body.token?.trim();
      if (!token) {
        res.status(400).json({ error: 'Approval token is required to apply' });
        return;
      }

      const { stdout, stderr, code } = await runInfraAgent(
        ['apply', id, '--token', token, '--json'],
        infraAgentDir,
        1800000,
      );

      try {
        const result = JSON.parse(stdout.trim());
        res.json({ ok: code === 0, ...result });
      } catch {
        res.json({
          ok: code === 0,
          output: stdout,
          error: stderr || undefined,
        });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
