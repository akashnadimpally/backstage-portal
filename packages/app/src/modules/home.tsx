import React, { useEffect, useState } from 'react';
import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { useApi, configApiRef, fetchApiRef } from '@backstage/core-plugin-api';
import {
  InfoCard,
  Progress,
  EmptyState,
  Link,
} from '@backstage/core-components';
import {
  Grid,
  Typography,
  Chip,
  Box,
  Tabs,
  Tab,
  Avatar,
  Tooltip,
  IconButton,
  TextField,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
} from '@material-ui/core';
import { makeStyles } from '@material-ui/core/styles';
import OpenInNewIcon from '@material-ui/icons/OpenInNew';
import CallMergeIcon from '@material-ui/icons/CallMerge';
import PlayCircleOutlineIcon from '@material-ui/icons/PlayCircleOutline';
import StarBorderIcon from '@material-ui/icons/StarBorder';
import ForkRightIcon from '@material-ui/icons/CallSplit';
import CheckCircleIcon from '@material-ui/icons/CheckCircle';
import ErrorIcon from '@material-ui/icons/Error';
import HourglassEmptyIcon from '@material-ui/icons/HourglassEmpty';
import GitHubIcon from '@material-ui/icons/GitHub';
import RefreshIcon from '@material-ui/icons/Refresh';

// --- Types ---
interface Repo {
  id: number;
  name: string;
  full_name: string;
  html_url: string;
  description: string | null;
  stargazers_count: number;
  forks_count: number;
  private: boolean;
  language: string | null;
  updated_at: string;
}

interface PullRequest {
  id: number;
  number: number;
  title: string;
  html_url: string;
  user: { login: string; avatar_url: string };
  repository_url: string;
  created_at: string;
  draft: boolean;
}

interface WorkflowRun {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  head_branch: string;
  updated_at: string;
  repository: { name: string; full_name: string };
}

// --- Styles ---
const useStyles = makeStyles(theme => ({
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: theme.spacing(3),
    paddingBottom: theme.spacing(2),
    borderBottom: `1px solid ${theme.palette.divider}`,
    flexWrap: 'wrap',
    gap: theme.spacing(2),
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
  },
  orgAvatar: {
    width: 52,
    height: 52,
    marginRight: theme.spacing(2),
    border: `2px solid ${theme.palette.primary.main}`,
  },
  orgTitle: {
    fontWeight: 700,
    lineHeight: 1.2,
  },
  orgSubtitle: {
    color: theme.palette.text.secondary,
    fontSize: '0.85rem',
    marginTop: 2,
  },
  repoItem: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    padding: theme.spacing(1.5, 0),
    borderBottom: `1px solid ${theme.palette.divider}`,
    '&:last-child': { borderBottom: 'none' },
  },
  repoName: {
    fontWeight: 600,
    fontSize: '0.9rem',
    color: theme.palette.primary.main,
    textDecoration: 'none',
    '&:hover': { textDecoration: 'underline' },
  },
  repoDesc: {
    color: theme.palette.text.secondary,
    fontSize: '0.8rem',
    marginTop: 2,
    maxWidth: 460,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  repoMeta: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 12,
    marginTop: 4,
  },
  langDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    display: 'inline-block',
    flexShrink: 0,
  },
  metaText: {
    fontSize: '0.75rem',
    color: theme.palette.text.secondary,
    display: 'flex',
    alignItems: 'center',
    gap: 3,
  },
  prItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: theme.spacing(1.5),
    padding: theme.spacing(1.5, 0),
    borderBottom: `1px solid ${theme.palette.divider}`,
    '&:last-child': { borderBottom: 'none' },
  },
  prAvatar: { width: 28, height: 28, marginTop: 2 },
  prTitle: {
    fontWeight: 500,
    fontSize: '0.875rem',
    color: theme.palette.text.primary,
    textDecoration: 'none',
    '&:hover': { textDecoration: 'underline', color: theme.palette.primary.main },
    display: 'inline',
  },
  prMeta: {
    fontSize: '0.75rem',
    color: theme.palette.text.secondary,
    marginTop: 3,
  },
  runItem: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: theme.spacing(1.5, 0),
    borderBottom: `1px solid ${theme.palette.divider}`,
    '&:last-child': { borderBottom: 'none' },
  },
  runName: {
    fontWeight: 500,
    fontSize: '0.875rem',
  },
  runMeta: {
    fontSize: '0.75rem',
    color: theme.palette.text.secondary,
    marginTop: 2,
  },
  statusSuccess: { color: '#2ea44f' },
  statusFailure: { color: '#d73a49' },
  statusPending: { color: '#dbab09' },
  statusNeutral: { color: '#6e7681' },
  badge: {
    fontSize: '0.65rem',
    height: 20,
    borderRadius: 4,
    textTransform: 'capitalize',
  },
  tabLabel: {
    textTransform: 'none',
    fontWeight: 600,
    fontSize: '0.85rem',
    minWidth: 100,
  },
  emptyBox: {
    padding: theme.spacing(4),
    textAlign: 'center',
    color: theme.palette.text.secondary,
  },
}));

// Language colors
const langColors: Record<string, string> = {
  TypeScript: '#3178c6', JavaScript: '#f1e05a', Python: '#3572A5',
  Go: '#00ADD8', Java: '#b07219', Rust: '#dea584', Ruby: '#701516',
  'C++': '#f34b7d', Shell: '#89e051', Kotlin: '#A97BFF',
};

// Proxy fetch hook using Backstage fetchApi (attaches Backstage Auth credentials for proxy endpoint)
function useProxyFetch() {
  const configApi = useApi(configApiRef);
  const fetchApi = useApi(fetchApiRef);
  const backendUrl = configApi.getString('backend.baseUrl');
  return async (path: string) => {
    const res = await fetchApi.fetch(`${backendUrl}/api/proxy/github/api${path}`);
    if (!res.ok) throw new Error(`GitHub API error ${res.status}: ${res.statusText}`);
    return res.json();
  };
}

// Repos tab
function ReposCard({ target }: { target: string }) {
  const classes = useStyles();
  const fetchProxy = useProxyFetch();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) { setLoading(false); return; }
    setLoading(true);
    setError(null);

    // Try org first, then user, then authenticated user
    fetchProxy(`/orgs/${target}/repos?sort=updated&per_page=30&type=all`)
      .catch(() => fetchProxy(`/users/${target}/repos?sort=updated&per_page=30`))
      .catch(() => fetchProxy(`/user/repos?sort=updated&per_page=30`))
      .then(d => { setRepos(d || []); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [target]);

  if (!target) return <Box className={classes.emptyBox}><Typography variant="body2">Select or enter a GitHub Org/User above.</Typography></Box>;
  if (loading) return <Progress />;
  if (error) return <Box className={classes.emptyBox}><Typography color="error">{error}</Typography><Typography variant="caption" display="block" style={{ marginTop: 8 }}>Ensure GITHUB_TOKEN has <code>repo</code> and <code>read:org</code> scopes.</Typography></Box>;
  if (!repos.length) return <EmptyState missing="data" title="No repositories" description={`No repos found for ${target}.`} />;

  return (
    <Box>
      {repos.map(r => (
        <Box key={r.id} className={classes.repoItem}>
          <Box>
            <Box display="flex" alignItems="center" style={{ gap: 8 }}>
              <a href={r.html_url} target="_blank" rel="noopener noreferrer" className={classes.repoName}>{r.name}</a>
              <Chip label={r.private ? 'Private' : 'Public'} size="small" className={classes.badge}
                style={{ background: r.private ? '#6e7681' : '#1f883d22', color: r.private ? '#fff' : '#1a7f37' }} />
            </Box>
            {r.description && <Typography className={classes.repoDesc}>{r.description}</Typography>}
            <Box className={classes.repoMeta}>
              {r.language && <Box className={classes.metaText}><span className={classes.langDot} style={{ background: langColors[r.language] ?? '#888' }} />{r.language}</Box>}
              <Box className={classes.metaText}><StarBorderIcon style={{ fontSize: 13 }} />{r.stargazers_count.toLocaleString()}</Box>
              <Box className={classes.metaText}><ForkRightIcon style={{ fontSize: 13 }} />{r.forks_count.toLocaleString()}</Box>
              <Box className={classes.metaText}>Updated {new Date(r.updated_at).toLocaleDateString()}</Box>
            </Box>
          </Box>
          <Tooltip title="Open in GitHub">
            <IconButton size="small" component="a" href={r.html_url} target="_blank" rel="noopener noreferrer">
              <OpenInNewIcon style={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        </Box>
      ))}
    </Box>
  );
}

// PRs tab
function PullRequestsCard({ target }: { target: string }) {
  const classes = useStyles();
  const fetchProxy = useProxyFetch();
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) { setLoading(false); return; }
    setLoading(true);
    setError(null);

    fetchProxy(`/search/issues?q=is:pr+is:open+org:${target}&sort=updated&per_page=20`)
      .catch(() => fetchProxy(`/search/issues?q=is:pr+is:open+user:${target}&sort=updated&per_page=20`))
      .then(d => { setPrs(d?.items ?? []); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [target]);

  const getRepo = (url: string) => url.split('/repos/')[1]?.split('/issues')[0] ?? '';

  if (!target) return <Box className={classes.emptyBox}><Typography variant="body2">Select or enter a GitHub Org/User above.</Typography></Box>;
  if (loading) return <Progress />;
  if (error) return <Box className={classes.emptyBox}><Typography color="error">{error}</Typography></Box>;
  if (!prs.length) return <EmptyState missing="data" title="No open pull requests" description={`No open PRs found for ${target}. 🎉`} />;

  return (
    <Box>
      {prs.map(pr => (
        <Box key={pr.id} className={classes.prItem}>
          <Avatar src={pr.user.avatar_url} className={classes.prAvatar} />
          <Box flex={1}>
            <Box display="flex" alignItems="flex-start" justifyContent="space-between">
              <Box flex={1} style={{ marginRight: 8 }}>
                <a href={pr.html_url} target="_blank" rel="noopener noreferrer" className={classes.prTitle}>{pr.title}</a>
                {pr.draft && <Chip label="Draft" size="small" className={classes.badge} style={{ marginLeft: 6, background: '#6e7681', color: '#fff' }} />}
              </Box>
              <Tooltip title="Open PR">
                <IconButton size="small" component="a" href={pr.html_url} target="_blank" rel="noopener noreferrer">
                  <OpenInNewIcon style={{ fontSize: 14 }} />
                </IconButton>
              </Tooltip>
            </Box>
            <Typography className={classes.prMeta}>
              #{pr.number} · {getRepo(pr.repository_url)} · by <strong>{pr.user.login}</strong> · {new Date(pr.created_at).toLocaleDateString()}
            </Typography>
          </Box>
        </Box>
      ))}
    </Box>
  );
}

// Pipelines tab
function PipelinesCard({ target }: { target: string }) {
  const classes = useStyles();
  const fetchProxy = useProxyFetch();
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) { setLoading(false); return; }
    setLoading(true);
    setError(null);

    const fetchRepos = fetchProxy(`/orgs/${target}/repos?sort=updated&per_page=5&type=all`)
      .catch(() => fetchProxy(`/users/${target}/repos?sort=updated&per_page=5`))
      .catch(() => fetchProxy(`/user/repos?sort=updated&per_page=5`));

    fetchRepos
      .then(async (repos: Repo[]) => {
        const groups = await Promise.all(
          (repos || []).map(r =>
            fetchProxy(`/repos/${r.full_name}/actions/runs?per_page=4`)
              .then((d: any) => (d.workflow_runs ?? []).map((run: any) => ({ ...run, repository: { name: r.name, full_name: r.full_name } })))
              .catch(() => [])
          )
        );
        const all = groups.flat().sort((a: WorkflowRun, b: WorkflowRun) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        );
        setRuns(all.slice(0, 20));
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [target]);

  const StatusIcon = ({ status, conclusion }: { status: string; conclusion: string | null }) => {
    if (status === 'completed') {
      if (conclusion === 'success') return <CheckCircleIcon className={classes.statusSuccess} style={{ fontSize: 18 }} />;
      if (conclusion === 'failure') return <ErrorIcon className={classes.statusFailure} style={{ fontSize: 18 }} />;
    }
    if (status === 'in_progress') return <HourglassEmptyIcon className={classes.statusPending} style={{ fontSize: 18 }} />;
    return <HourglassEmptyIcon className={classes.statusNeutral} style={{ fontSize: 18 }} />;
  };

  if (!target) return <Box className={classes.emptyBox}><Typography variant="body2">Select or enter a GitHub Org/User above.</Typography></Box>;
  if (loading) return <Progress />;
  if (error) return <Box className={classes.emptyBox}><Typography color="error">{error}</Typography></Box>;
  if (!runs.length) return <EmptyState missing="data" title="No workflow runs" description={`No GitHub Actions runs found for ${target}.`} />;

  return (
    <Box>
      {runs.map(run => (
        <Box key={run.id} className={classes.runItem}>
          <Box display="flex" alignItems="center" style={{ gap: 12 }}>
            <StatusIcon status={run.status} conclusion={run.conclusion} />
            <Box>
              <Typography className={classes.runName}>{run.name}</Typography>
              <Typography className={classes.runMeta}>{run.repository.name} · {run.head_branch} · {new Date(run.updated_at).toLocaleString()}</Typography>
            </Box>
          </Box>
          <Box display="flex" alignItems="center" style={{ gap: 8 }}>
            <Chip
              label={run.status === 'in_progress' ? 'Running' : (run.conclusion ?? run.status)}
              size="small"
              className={classes.badge}
              style={{
                background: run.conclusion === 'success' ? '#1f883d22' : run.status === 'in_progress' ? '#0078d422' : run.conclusion === 'failure' ? '#d73a4922' : '#6e768122',
                color: run.conclusion === 'success' ? '#1a7f37' : run.status === 'in_progress' ? '#0078d4' : run.conclusion === 'failure' ? '#d73a49' : '#6e7681',
              }}
            />
            <Tooltip title="Open run">
              <IconButton size="small" component="a" href={run.html_url} target="_blank" rel="noopener noreferrer">
                <OpenInNewIcon style={{ fontSize: 14 }} />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>
      ))}
    </Box>
  );
}

// Main dashboard component
function GitHubOrgDashboard() {
  const classes = useStyles();
  const fetchProxy = useProxyFetch();
  const [tab, setTab] = useState(0);
  const [target, setTarget] = useState<string>('akash-sky18');
  const [customInput, setCustomInput] = useState<string>('');
  const [orgOptions, setOrgOptions] = useState<Array<{ login: string; avatar: string }>>([
    { login: 'akash-sky18', avatar: 'https://github.com/akash-sky18.png' }
  ]);

  // Auto-discover user & orgs from GitHub token on mount
  useEffect(() => {
    Promise.all([
      fetchProxy('/user/orgs').catch(() => []),
      fetchProxy('/user').catch(() => null),
    ]).then(([orgs, user]) => {
      const opts: Array<{ login: string; avatar: string }> = [];
      if (Array.isArray(orgs)) {
        orgs.forEach(o => opts.push({ login: o.login, avatar: o.avatar_url }));
      }
      if (user?.login) {
        opts.push({ login: user.login, avatar: user.avatar_url });
      }
      if (opts.length > 0) {
        setOrgOptions(opts);
        // Default to first org or akash-sky18
        const defaultOrg = opts.find(o => o.login === 'akash-sky18')?.login || opts[0].login;
        setTarget(defaultOrg);
      }
    });
  }, []);

  const handleCustomSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customInput.trim()) {
      const val = customInput.trim();
      setTarget(val);
      if (!orgOptions.some(o => o.login === val)) {
        setOrgOptions(prev => [...prev, { login: val, avatar: `https://github.com/${val}.png` }]);
      }
      setCustomInput('');
    }
  };

  return (
    <Box style={{ padding: '0 8px' }}>
      <Box className={classes.header}>
        <Box className={classes.headerLeft}>
          <Avatar src={`https://github.com/${target}.png?size=52`} className={classes.orgAvatar}>
            <GitHubIcon />
          </Avatar>
          <Box>
            <Typography variant="h5" className={classes.orgTitle}>
              {target}
            </Typography>
            <Typography className={classes.orgSubtitle}>
              <Link to={`https://github.com/${target}`} target="_blank" rel="noopener noreferrer">
                github.com/{target}
              </Link>
            </Typography>
          </Box>
        </Box>

        <Box display="flex" alignItems="center" style={{ gap: 12 }}>
          <FormControl variant="outlined" size="small" style={{ minWidth: 160 }}>
            <InputLabel id="org-select-label">Select Account/Org</InputLabel>
            <Select
              labelId="org-select-label"
              value={target}
              onChange={(e) => setTarget(e.target.value as string)}
              label="Select Account/Org"
            >
              {orgOptions.map(opt => (
                <MenuItem key={opt.login} value={opt.login}>
                  <Box display="flex" alignItems="center" style={{ gap: 8 }}>
                    <Avatar src={opt.avatar} style={{ width: 20, height: 20 }} />
                    {opt.login}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <form onSubmit={handleCustomSubmit} style={{ display: 'flex', gap: 4 }}>
            <TextField
              size="small"
              variant="outlined"
              placeholder="Or enter org/user..."
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              style={{ width: 160 }}
            />
          </form>
        </Box>
      </Box>

      <Tabs
        value={tab}
        onChange={(_: React.ChangeEvent<{}>, v: number) => setTab(v)}
        indicatorColor="primary"
        textColor="primary"
        style={{ marginBottom: 16, borderBottom: '1px solid #e0e0e0' }}
      >
        <Tab className={classes.tabLabel} icon={<StarBorderIcon style={{ fontSize: 16 }} />} label="Repositories" />
        <Tab className={classes.tabLabel} icon={<CallMergeIcon style={{ fontSize: 16 }} />} label="Pull Requests" />
        <Tab className={classes.tabLabel} icon={<PlayCircleOutlineIcon style={{ fontSize: 16 }} />} label="Pipelines" />
      </Tabs>

      {tab === 0 && <ReposCard target={target} />}
      {tab === 1 && <PullRequestsCard target={target} />}
      {tab === 2 && <PipelinesCard target={target} />}
    </Box>
  );
}

// Full page wrapper
function GitHubOrgPage() {
  return (
    <Box style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <Grid container spacing={3}>
        <Grid item xs={12}>
          <InfoCard title="GitHub Overview" noPadding={false}>
            <GitHubOrgDashboard />
          </InfoCard>
        </Grid>
      </Grid>
    </Box>
  );
}

// GitHub Org page extension
const githubOrgPage = PageBlueprint.make({
  name: 'github-org',
  params: {
    path: '/github-org',
    title: 'GitHub Org',
    icon: <GitHubIcon />,
    loader: async () => <GitHubOrgPage />,
  },
});

// Frontend module
export const homeModule = createFrontendModule({
  pluginId: 'app',
  extensions: [githubOrgPage],
});
