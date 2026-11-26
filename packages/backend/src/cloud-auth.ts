import express from 'express';
import { Router } from 'express';
import { exec, spawn } from 'child_process';
import util from 'util';

const execPromise = util.promisify(exec);
const subscriptionIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
let azureLoginInProgress = false;

async function getCurrentCost(subscriptionId: string, accessToken: string) {
  const body = {
    type: 'Usage',
    timeframe: 'MonthToDate',
    dataset: {
      granularity: 'Daily',
      aggregation: {
        totalCost: { name: 'PreTaxCost', function: 'Sum' },
      },
      grouping: [{ type: 'Dimension', name: 'ResourceGroup' }],
    },
  };

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await fetch(
      `https://management.azure.com/subscriptions/${subscriptionId}/providers/Microsoft.CostManagement/query?api-version=2023-03-01`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      },
    );

    if (response.status === 429 && attempt < 2) {
      const retryAfter = Number(response.headers.get('retry-after'));
      await new Promise(resolve =>
        setTimeout(
          resolve,
          retryAfter > 0
            ? Math.min(retryAfter, 10) * 1000
            : 1000 * 2 ** attempt,
        ),
      );
      continue;
    }

    if (response.status === 204) {
      return { currentCost: 'No usage data', currency: '' };
    }
    if (!response.ok) {
      throw new Error(`Cost Management returned HTTP ${response.status}`);
    }

    const result = await response.json();
    const columns = result.properties?.columns?.map((column: { name: string }) =>
      column.name.toLowerCase(),
    ) ?? [];
    const costIndex = columns.findIndex((name: string) => name === 'totalcost' || name === 'pretaxcost');
    const currencyIndex = columns.indexOf('currency');
    const totals = new Map<string, number>();

    for (const row of result.properties?.rows ?? []) {
      const currency = String(row[currencyIndex] || 'USD');
      totals.set(currency, (totals.get(currency) ?? 0) + Number(row[costIndex] ?? 0));
    }

    if (totals.size === 0) {
      return { currentCost: 'No usage data', currency: '' };
    }

    const values = [...totals.entries()];
    return {
      currentCost: values
        .map(([currency, amount]) =>
          new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount),
        )
        .join(', '),
      currency: values.map(([currency]) => currency).join(', '),
    };
  }

  throw new Error('Cost Management request was throttled');
}

interface CostManagementResult {
  value?: Array<{
    properties?: {
      billingPeriodStartDate?: string;
      billingPeriodEndDate?: string;
    };
  }>;
  properties?: {
    columns?: Array<{ name: string }>;
    rows?: unknown[][];
  };
}

async function requestAzureJson(
  url: string,
  accessToken: string,
  init?: RequestInit,
): Promise<CostManagementResult | undefined> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await fetch(url, {
      ...init,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });

    if (response.status === 429 && attempt < 2) {
      const retryAfter = Number(response.headers.get('retry-after'));
      await new Promise(resolve =>
        setTimeout(
          resolve,
          retryAfter > 0
            ? Math.min(retryAfter, 10) * 1000
            : 1000 * 2 ** attempt,
        ),
      );
      continue;
    }

    if (response.status === 204) return undefined;
    if (!response.ok) {
      throw new Error(`Azure billing API returned HTTP ${response.status}`);
    }
    return (await response.json()) as CostManagementResult;
  }

  throw new Error('Azure billing API request was throttled');
}

function formatForecastCost(result: CostManagementResult | undefined): string {
  const columns = result?.properties?.columns?.map(column =>
    column.name.toLowerCase(),
  ) ?? [];
  const costIndex = columns.findIndex(name =>
    ['totalcost', 'pretaxcost', 'cost'].includes(name),
  );
  if (costIndex < 0) {
    if (!result?.properties?.rows?.length) return 'No forecast data';
    throw new Error('Azure forecast response did not include a cost column');
  }

  const currencyIndex = columns.indexOf('currency');
  const totals = new Map<string, number>();
  for (const row of result?.properties?.rows ?? []) {
    const currency = String(
      currencyIndex >= 0 ? row[currencyIndex] ?? '' : '',
    );
    const amount = Number(row[costIndex] ?? 0);
    if (!Number.isFinite(amount)) {
      throw new Error('Azure forecast response included a non-numeric cost');
    }
    totals.set(currency, (totals.get(currency) ?? 0) + amount);
  }

  if (totals.size === 0) return 'No forecast data';
  return [...totals.entries()]
    .map(([currency, amount]) =>
      currency
        ? new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency,
          }).format(amount)
        : `${amount.toFixed(2)} (currency unavailable)`,
    )
    .join(', ');
}

async function getForecastCost(subscriptionId: string, accessToken: string) {
  const result = await requestAzureJson(
    `https://management.azure.com/subscriptions/${subscriptionId}/providers/Microsoft.CostManagement/forecast?api-version=2023-03-01`,
    accessToken,
    {
      method: 'POST',
      body: JSON.stringify({
        type: 'Usage',
        timeframe: 'MonthToDate',
        includeActualCost: true,
        dataset: {
          granularity: 'Daily',
          aggregation: {
            totalCost: { name: 'Cost', function: 'Sum' },
          },
        },
      }),
    },
  );
  return formatForecastCost(result);
}

async function getCurrentBillingPeriod(
  subscriptionId: string,
  accessToken: string,
) {
  const result = await requestAzureJson(
    `https://management.azure.com/subscriptions/${subscriptionId}/providers/Microsoft.Billing/billingPeriods?api-version=2017-04-24-preview`,
    accessToken,
  );
  const periods = result?.value ?? [];
  const today = new Date().toISOString().slice(0, 10);
  const currentPeriod = periods.find(period => {
    const startDate = period.properties?.billingPeriodStartDate?.slice(0, 10);
    const endDate = period.properties?.billingPeriodEndDate?.slice(0, 10);
    return Boolean(
      startDate &&
        endDate &&
        /^\d{4}-\d{2}-\d{2}$/.test(startDate) &&
        /^\d{4}-\d{2}-\d{2}$/.test(endDate) &&
        startDate <= today &&
        endDate >= today,
    );
  });

  return {
    billingPeriodStartDate:
      currentPeriod?.properties?.billingPeriodStartDate?.slice(0, 10) ??
      'Unavailable',
    billingPeriodEndDate:
      currentPeriod?.properties?.billingPeriodEndDate?.slice(0, 10) ??
      'Unavailable',
  };
}

export function createCloudAuthRouter(): Router {
  const router = Router();
  router.use(express.json());

  // Azure Status endpoint
  router.get('/azure/status', async (_req, res) => {
    try {
      const { stdout } = await execPromise('az account show --output json');
      const account = JSON.parse(stdout);
      res.json({
        authenticated: true,
        user: account.user?.name || account.name,
      });
    } catch {
      res.json({ authenticated: false });
    }
  });

  // Start an interactive device-code login so the browser can display the instructions.
  router.post('/azure/login', async (_req, res) => {
    if (azureLoginInProgress) {
      return res.status(409).json({ error: 'An Azure login is already in progress' });
    }

    azureLoginInProgress = true;
    const child = spawn(
      'az',
      ['login', '--use-device-code', '--output', 'json'],
      { shell: process.platform === 'win32', windowsHide: true },
    );
    let output = '';
    let responseSent = false;
    const timeout = setTimeout(() => {
      azureLoginInProgress = false;
      child.kill();
      if (!responseSent) {
        responseSent = true;
        res.status(504).json({ error: 'Azure CLI did not provide a device code in time' });
      }
    }, 30000);
    const finish = () => {
      clearTimeout(timeout);
      azureLoginInProgress = false;
    };
    const handleOutput = (chunk: Buffer) => {
      output = `${output}${chunk.toString()}`.slice(-4096);
      const match = output.match(
        /https:\/\/microsoft\.com\/devicelogin[\s\S]{0,200}?enter the code\s+([A-Z0-9]+)/i,
      );
      if (match && !responseSent) {
        responseSent = true;
        res.json({
          status: 'pending',
          verificationUri: 'https://microsoft.com/devicelogin',
          userCode: match[1],
        });
      }
    };

    child.stdout?.on('data', handleOutput);
    child.stderr?.on('data', handleOutput);
    child.on('error', () => {
      finish();
      if (!responseSent) {
        responseSent = true;
        res.status(500).json({ error: 'Unable to start Azure CLI device-code login' });
      }
    });
    child.on('exit', code => {
      finish();
      if (!responseSent) {
        responseSent = true;
        res.status(500).json({ error: `Azure CLI login exited before providing a device code (${code})` });
      }
    });
  });

  // Azure Subscriptions list endpoint (with cost & forecasted values)
  router.get('/azure/subscriptions', async (_req, res) => {
    try {
      const { stdout } = await execPromise('az account list --all --output json');
      const accounts = JSON.parse(stdout);
      const { stdout: tokenOutput } = await execPromise(
        'az account get-access-token --resource https://management.azure.com/ --query accessToken --output tsv',
      );
      const accessToken = tokenOutput.trim();

      const subscriptions = await Promise.all(
        accounts.map(async (acc: any) => {
          let cost = { currentCost: 'Unavailable', currency: '' };
          let forecastCost = 'Unavailable';
          let billingPeriod = {
            billingPeriodStartDate: 'Unavailable',
            billingPeriodEndDate: 'Unavailable',
          };
          if (acc.state === 'Enabled' && subscriptionIdPattern.test(acc.id)) {
            const [currentCost, currentForecast, currentBillingPeriod] =
              await Promise.all([
                getCurrentCost(acc.id, accessToken).catch(error => {
                  console.warn(
                    `[Azure Cost] Current cost unavailable for ${acc.id}: ${String(error)}`,
                  );
                  return { currentCost: 'Unavailable', currency: '' };
                }),
                getForecastCost(acc.id, accessToken).catch(error => {
                  console.warn(
                    `[Azure Cost] Forecast unavailable for ${acc.id}: ${String(error)}`,
                  );
                  return 'Unavailable';
                }),
                getCurrentBillingPeriod(acc.id, accessToken).catch(error => {
                  console.warn(
                    `[Azure Billing] Billing period unavailable for ${acc.id}: ${String(error)}`,
                  );
                  return {
                    billingPeriodStartDate: 'Unavailable',
                    billingPeriodEndDate: 'Unavailable',
                  };
                }),
              ]);
            cost = currentCost;
            forecastCost = currentForecast;
            billingPeriod = currentBillingPeriod;
          }

          return {
            id: acc.id,
            name: acc.name,
            state: acc.state,
            tenantDisplayName: acc.tenantDisplayName || acc.tenantDefaultDomain,
            user: acc.user?.name,
            isDefault: acc.isDefault,
            ...cost,
            forecastCost,
            ...billingPeriod,
          };
        }),
      );

      res.json({ subscriptions });
    } catch (err: any) {
      res.status(401).json({ error: 'Authentication required. Please log in via Azure CLI.' });
    }
  });

  router.get('/azure/resource-groups', async (req, res) => {
    const subscriptionId = req.query.subscriptionId as string;
    if (!subscriptionId || !subscriptionIdPattern.test(subscriptionId)) {
      return res.status(400).json({ error: 'A valid subscriptionId is required' });
    }

    try {
      const { stdout } = await execPromise(
        `az group list --subscription ${subscriptionId} --output json`,
      );
      const groups = JSON.parse(stdout).map((group: any) => ({
        id: group.id,
        name: group.name,
        location: group.location,
      }));
      res.json({ subscriptionId, count: groups.length, resourceGroups: groups });
    } catch {
      res.status(502).json({ error: 'Unable to list resource groups for this subscription' });
    }
  });

  // Azure Resources endpoint per subscription selection
  router.get('/azure/resources', async (req, res) => {
    const subscriptionId = req.query.subscriptionId as string;
    if (!subscriptionId || !subscriptionIdPattern.test(subscriptionId)) {
      return res.status(400).json({ error: 'A valid subscriptionId is required' });
    }

    try {
      const { stdout } = await execPromise(`az resource list --subscription ${subscriptionId} --output json`);
      const rawResources = JSON.parse(stdout);

      const resources = rawResources.map((r: any) => ({
        id: r.id,
        name: r.name,
        type: r.type,
        location: r.location,
        resourceGroup: r.resourceGroup,
        provisioningState: r.provisioningState,
      }));

      res.json({ subscriptionId, count: resources.length, resources });
    } catch (err: any) {
      // Fallback response if session needs refresh or subscription has 0 resources
      res.json({ subscriptionId, count: 0, resources: [], message: err.message });
    }
  });

  return router;
}
