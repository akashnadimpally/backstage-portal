import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  CircularProgress,
  FormControl,
  Grid,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@material-ui/core';
import BuildIcon from '@material-ui/icons/Build';
import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { discoveryApiRef, fetchApiRef, useApi } from '@backstage/core-plugin-api';
import { Content, ContentHeader, Page } from '@backstage/core-components';

type ProviderId = 'azure-devops' | 'github' | 'gitlab';
type Provider = { id: ProviderId; label: string; configured: boolean; organizations?: string[] };
type Project = { id: string; name: string; defaultBranch?: string };
type Pipeline = { id: string; name: string; path?: string; state?: string };
type Run = {
  id: string;
  name: string;
  state: string;
  result?: string;
  branch?: string;
  commit?: string;
  createdAt?: string;
  finishedAt?: string;
};
type Analysis = {
  report: string;
  certification: { certified: boolean; scanners: Array<{ scanner: string; found: boolean; passed: boolean }> };
};

const providerReadPermission = {
  name: 'pipelines.read',
  attributes: { action: 'read' as const },
};

function isComplete(run: Run) {
  return ['completed', 'success', 'failed', 'canceled', 'cancelled', 'skipped'].includes(run.state.toLowerCase());
}

function scannerStatus(scanner: Analysis['certification']['scanners'][number]) {
  if (!scanner.found) return 'missing';
  return scanner.passed ? 'passed' : 'failed';
}

function PipelinesPage() {
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState<ProviderId>('azure-devops');
  const [organization, setOrganization] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState('');
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [pipelineId, setPipelineId] = useState('');
  const [branch, setBranch] = useState('main');
  const [runs, setRuns] = useState<Run[]>([]);
  const [tracking, setTracking] = useState<{ id: string; branch: string; since: number } | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const api = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const baseUrl = await discoveryApi.getBaseUrl('pipelines');
    const response = await fetchApi.fetch(`${baseUrl}${path}`, init);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed with HTTP ${response.status}`);
    return result as T;
  }, [discoveryApi, fetchApi]);

  const query = useCallback(() => {
    const params = new URLSearchParams({ provider, project });
    if (provider === 'azure-devops') params.set('organization', organization);
    return params;
  }, [provider, project, organization]);

  useEffect(() => {
    void api<{ providers: Provider[] }>('/providers')
      .then(result => {
        setProviders(result.providers);
        const firstConfigured = result.providers.find(item => item.configured);
        if (firstConfigured) {
          setProvider(firstConfigured.id);
          setOrganization(firstConfigured.organizations?.[0] || '');
        }
      })
      .catch(cause => setError(cause instanceof Error ? cause.message : 'Unable to load providers'));
  }, [api]);

  useEffect(() => {
    const currentProvider = providers.find(item => item.id === provider);
    if (!currentProvider?.configured || (provider === 'azure-devops' && !organization)) {
      setProjects([]);
      setProject('');
      setPipelines([]);
      setPipelineId('');
      return;
    }
    setLoading(true);
    setError('');
    const params = new URLSearchParams({ provider });
    if (provider === 'azure-devops') params.set('organization', organization);
    void api<{ projects: Project[] }>(`/projects?${params}`)
      .then(result => setProjects(result.projects))
      .catch(cause => setError(cause instanceof Error ? cause.message : 'Unable to load projects'))
      .finally(() => setLoading(false));
  }, [api, provider, organization, providers]);

  useEffect(() => {
    if (!project) {
      setPipelines([]);
      setPipelineId('');
      setRuns([]);
      return;
    }
    setLoading(true);
    setError('');
    void api<{ pipelines: Pipeline[] }>(`/pipelines?${query()}`)
      .then(result => {
        setPipelines(result.pipelines);
        setPipelineId(result.pipelines[0]?.id || '');
      })
      .catch(cause => setError(cause instanceof Error ? cause.message : 'Unable to load pipelines'))
      .finally(() => setLoading(false));
  }, [api, project, provider, organization, query]);

  const refreshRuns = useCallback(async () => {
    if (!project || !pipelineId) return [] as Run[];
    const result = await api<{ runs: Run[] }>(`/runs?${query()}&pipelineId=${encodeURIComponent(pipelineId)}`);
    setRuns(result.runs);
    return result.runs;
  }, [api, project, pipelineId, query]);

  useEffect(() => {
    setAnalysis(null);
    setRuns([]);
    if (project && pipelineId) void refreshRuns().catch(cause => setError(cause instanceof Error ? cause.message : 'Unable to load runs'));
  }, [project, pipelineId, provider, organization, refreshRuns]);

  useEffect(() => {
    if (!tracking) return undefined;
    let analyzing = false;
    const timer = window.setInterval(() => {
      void refreshRuns().then(async latestRuns => {
        const matchingRun = latestRuns.find(run => tracking.id ? run.id === tracking.id :
          run.branch?.replace(/^refs\/heads\//, '') === tracking.branch &&
          (Date.parse(run.createdAt || '') >= tracking.since - 30_000));
        if (!matchingRun) return;
        if (!isComplete(matchingRun) || analyzing) return;
        analyzing = true;
        window.clearInterval(timer);
        setTracking(null);
        setRunning(false);
        try {
          const result = await api<Analysis>('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...Object.fromEntries(query()), pipelineId, runId: matchingRun.id, branch: tracking.branch }),
          });
          setAnalysis(result);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : 'Automatic pipeline analysis failed');
        }
      }).catch(cause => setError(cause instanceof Error ? cause.message : 'Unable to refresh pipeline status'));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [tracking, provider, organization, project, pipelineId, api, query, refreshRuns]);

  const startRun = async () => {
    setRunning(true);
    setAnalysis(null);
    setError('');
    setNotice('Pipeline queued. Waiting for completion before analysis.');
    const since = Date.now();
    try {
      const result = await api<{ id: string; state: string }>('/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...Object.fromEntries(query()), pipelineId, branch }),
      });
      setTracking({ id: result.id, branch, since });
      if (result.id) void refreshRuns();
    } catch (cause) {
      setRunning(false);
      setNotice('');
      setError(cause instanceof Error ? cause.message : 'Unable to start pipeline');
    }
  };

  const selectedProvider = providers.find(item => item.id === provider);
  const selectedPipeline = pipelines.find(item => item.id === pipelineId);

  return (
    <Page themeId="home">
      <Content>
        <ContentHeader title="Pipelines" description="Run and review CI pipelines across your configured providers." />
        <Grid container spacing={2}>
          <Grid item xs={12} md={3}>
            <FormControl fullWidth variant="outlined">
              <InputLabel id="pipeline-provider-label">Provider</InputLabel>
              <Select labelId="pipeline-provider-label" label="Provider" value={provider} onChange={event => {
                setProvider(event.target.value as ProviderId);
                const next = providers.find(item => item.id === event.target.value);
                setOrganization(next?.organizations?.[0] || '');
                setProject('');
              }}>
                {providers.map(item => <MenuItem key={item.id} value={item.id} disabled={!item.configured}>{item.label}{item.configured ? '' : ' (not configured)'}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          {provider === 'azure-devops' && (
            <Grid item xs={12} md={3}>
              <FormControl fullWidth variant="outlined">
                <InputLabel id="pipeline-org-label">Organization</InputLabel>
                <Select labelId="pipeline-org-label" label="Organization" value={organization} onChange={event => { setOrganization(String(event.target.value)); setProject(''); }}>
                  {(selectedProvider?.organizations || []).map(org => <MenuItem key={org} value={org}>{org}</MenuItem>)}
                </Select>
              </FormControl>
            </Grid>
          )}
          <Grid item xs={12} md={provider === 'azure-devops' ? 3 : 4}>
            <FormControl fullWidth variant="outlined" disabled={loading || projects.length === 0}>
              <InputLabel id="pipeline-project-label">{provider === 'github' ? 'Repository' : 'Project'}</InputLabel>
              <Select labelId="pipeline-project-label" label={provider === 'github' ? 'Repository' : 'Project'} value={project} onChange={event => setProject(String(event.target.value))}>
                {projects.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} md={provider === 'azure-devops' ? 3 : 4}>
            <FormControl fullWidth variant="outlined" disabled={loading || pipelines.length === 0}>
              <InputLabel id="pipeline-definition-label">Pipeline</InputLabel>
              <Select labelId="pipeline-definition-label" label="Pipeline" value={pipelineId} onChange={event => setPipelineId(String(event.target.value))}>
                {pipelines.map(item => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField fullWidth variant="outlined" label="Branch or tag" value={branch} onChange={event => setBranch(event.target.value)} />
          </Grid>
          <Grid item xs={12} md={6}>
            <Button fullWidth color="primary" variant="contained" disabled={!selectedPipeline || running || loading || !branch.trim()} onClick={startRun} style={{ minHeight: 56 }}>
              {running ? <><CircularProgress size={18} style={{ marginRight: 8 }} /> Waiting for pipeline</> : 'Run pipeline'}
            </Button>
          </Grid>
          {error && <Grid item xs={12}><Typography color="error" role="alert">{error}</Typography></Grid>}
          {notice && <Grid item xs={12}><Typography role="status">{notice}</Typography></Grid>}
          <Grid item xs={12}>
            <Paper>
              <Table size="small">
                <TableHead><TableRow><TableCell>Run</TableCell><TableCell>Branch</TableCell><TableCell>Status</TableCell><TableCell>Result</TableCell><TableCell>Started</TableCell></TableRow></TableHead>
                <TableBody>
                  {runs.map(run => <TableRow key={run.id}><TableCell>{run.name || run.id}</TableCell><TableCell>{run.branch || '-'}</TableCell><TableCell>{run.state}</TableCell><TableCell>{run.result || '-'}</TableCell><TableCell>{run.createdAt ? new Date(run.createdAt).toLocaleString() : '-'}</TableCell></TableRow>)}
                  {runs.length === 0 && <TableRow><TableCell colSpan={5}>{loading ? 'Loading pipelines…' : 'No runs found.'}</TableCell></TableRow>}
                </TableBody>
              </Table>
            </Paper>
          </Grid>
          {analysis && (
            <Grid item xs={12}>
              <Paper style={{ padding: 20 }}>
                <Typography variant="h6" gutterBottom>Pipeline agent report</Typography>
                <Typography variant="subtitle1" color={analysis.certification.certified ? 'primary' : 'error'}>
                  {analysis.certification.certified ? 'Security checks passed' : 'Build not certified'}
                </Typography>
                <Typography variant="body2" paragraph>
                  {analysis.certification.scanners.map(scanner => `${scanner.scanner}: ${scannerStatus(scanner)}`).join(' · ')}
                </Typography>
                <Typography component="pre" style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}>{analysis.report}</Typography>
              </Paper>
            </Grid>
          )}
        </Grid>
      </Content>
    </Page>
  );
}

const pipelinesPage = PageBlueprint.make({
  name: 'pipelines',
  params: {
    path: '/pipelines',
    title: 'Pipelines',
    icon: <BuildIcon />,
    loader: async () => <PipelinesPage />,
  },
});

export const pipelinesModule = createFrontendModule({
  pluginId: 'app',
  extensions: [pipelinesPage],
});