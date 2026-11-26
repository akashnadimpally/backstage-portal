import { useCallback, useEffect, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  Paper,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@material-ui/core';
import LayersIcon from '@material-ui/icons/Layers';
import RefreshIcon from '@material-ui/icons/Refresh';
import AddIcon from '@material-ui/icons/Add';
import CheckCircleIcon from '@material-ui/icons/CheckCircle';
import ErrorOutlineIcon from '@material-ui/icons/ErrorOutline';
import HourglassEmptyIcon from '@material-ui/icons/HourglassEmpty';
import PlayArrowIcon from '@material-ui/icons/PlayArrow';
import VerifiedUserIcon from '@material-ui/icons/VerifiedUser';
import ExpandMoreIcon from '@material-ui/icons/ExpandMore';
import CodeIcon from '@material-ui/icons/Code';
import SearchIcon from '@material-ui/icons/Search';

import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { discoveryApiRef, fetchApiRef, useApi } from '@backstage/core-plugin-api';
import { Content, ContentHeader, Page, SupportButton } from '@backstage/core-components';

interface InfraAgentStatus {
  available: boolean;
  infraAgentDir: string;
  pythonBin: string;
  hasVenv: boolean;
  activeProvider: string;
  isPlaceholder: boolean;
  moduleCount: number;
  policy?: {
    allowed_locations?: string[];
    allowed_modules?: string[];
    max_module_calls?: number;
    max_resource_changes?: number;
    required_tags?: string[];
  };
  models?: Record<string, string>;
}

interface RequestSummary {
  id: string;
  status: string;
  statusMessage?: string;
  description?: string;
  environment?: string;
  location?: string;
  requestedBy?: string;
  planSummary?: {
    counts?: Record<string, number>;
    changes?: Array<{ action: string; address: string }>;
  };
  result?: {
    status?: string;
    outputs?: Record<string, any>;
  };
  createdAt?: string;
}

interface RequestDetail {
  id: string;
  dir: string;
  state?: {
    status: string;
    status_message?: string;
    created_at?: string;
    plan_sha256?: string;
  };
  request?: {
    description: string;
    environment: string;
    location: string;
    naming_prefix: string;
    requested_by: string;
    tags?: Record<string, string>;
  };
  requirements?: any;
  generatedConfig?: any;
  review?: {
    status?: string;
    verdict?: string;
    findings?: Array<{ severity: string; message: string }>;
  };
  planSummary?: {
    counts?: Record<string, number>;
    changes?: Array<{ action: string; address: string }>;
  };
  result?: {
    status?: string;
    outputs?: Record<string, any>;
  };
  tfFiles?: Record<string, string>;
  auditLogs?: any[];
  availableLogs?: string[];
}

interface ModuleInfo {
  enabled: boolean;
  variables: string[];
  required: string[];
  optional: string[];
  outputs: string[];
  types?: Record<string, string>;
  defaults?: Record<string, any>;
  descriptions?: Record<string, string>;
}

const DEFAULT_SAMPLE_REQUEST = `description: "Deploy an Azure Container Registry and an Azure Kubernetes Service cluster connected to a Virtual Network"
environment: "dev"
location: "eastus"
naming_prefix: "skynet"
requested_by: "Engineering Team"
tags:
  cost_center: "101"
  managed_by: "infra-agent"
`;

function statusColor(status: string): 'default' | 'primary' | 'secondary' {
  const s = status?.toLowerCase() || '';
  if (s.includes('deployed') || s.includes('approved') || s.includes('applied')) return 'primary';
  if (s.includes('awaiting_approval') || s.includes('running') || s.includes('planning')) return 'secondary';
  return 'default';
}

function StatusIcon({ status }: { status: string }) {
  const s = status?.toLowerCase() || '';
  if (s.includes('deployed') || s.includes('approved')) return <CheckCircleIcon style={{ color: '#4caf50', verticalAlign: 'middle', marginRight: 4 }} />;
  if (s.includes('awaiting_approval')) return <HourglassEmptyIcon style={{ color: '#ff9800', verticalAlign: 'middle', marginRight: 4 }} />;
  if (s.includes('failed') || s.includes('needs_user')) return <ErrorOutlineIcon style={{ color: '#f44336', verticalAlign: 'middle', marginRight: 4 }} />;
  return <PlayArrowIcon style={{ color: '#2196f3', verticalAlign: 'middle', marginRight: 4 }} />;
}

export function InfraAgentPage() {
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);

  const [tab, setTab] = useState(0);
  const [status, setStatus] = useState<InfraAgentStatus | null>(null);
  const [requests, setRequests] = useState<RequestSummary[]>([]);
  const [catalog, setCatalog] = useState<Record<string, ModuleInfo>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notification, setNotification] = useState<string | null>(null);

  // New Request Dialog
  const [openNewDialog, setOpenNewDialog] = useState(false);
  const [requestYaml, setRequestYaml] = useState(DEFAULT_SAMPLE_REQUEST);
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<{ valid: boolean; message?: string; error?: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Request Details Dialog
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<RequestDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [approving, setApproving] = useState(false);
  const [applying, setApplying] = useState(false);
  const [approvalToken, setApprovalToken] = useState<string | null>(null);
  const [selectedTfTab, setSelectedTfTab] = useState<string>('main.tf');

  // Catalog search
  const [catalogSearch, setCatalogSearch] = useState('');

  const api = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const baseUrl = await discoveryApi.getBaseUrl('infra-agent');
    const response = await fetchApi.fetch(`${baseUrl}${path}`, init);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed with HTTP ${response.status}`);
    return result as T;
  }, [discoveryApi, fetchApi]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, reqsRes, catRes] = await Promise.all([
        api<InfraAgentStatus>('/status'),
        api<{ requests: RequestSummary[] }>('/requests'),
        api<{ modules: Record<string, ModuleInfo> }>('/catalog'),
      ]);
      setStatus(statusRes);
      setRequests(reqsRes.requests || []);
      setCatalog(catRes.modules || {});
    } catch (err: any) {
      setError(err.message || 'Failed to load Infra Agent data');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleOpenDetail = async (id: string) => {
    setSelectedRequestId(id);
    setLoadingDetail(true);
    setApprovalToken(null);
    try {
      const detail = await api<RequestDetail>(`/requests/${id}`);
      setSelectedRequest(detail);
      const tfKeys = Object.keys(detail.tfFiles || {});
      if (tfKeys.length > 0) setSelectedTfTab(tfKeys[0]);
    } catch (err: any) {
      setError(err.message || 'Failed to load request details');
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleValidate = async () => {
    setValidating(true);
    setValidationResult(null);
    try {
      const res = await api<{ valid: boolean; message?: string }>('/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: requestYaml }),
      });
      setValidationResult({ valid: true, message: res.message || 'Request syntax and policy validation passed.' });
    } catch (err: any) {
      setValidationResult({ valid: false, error: err.message });
    } finally {
      setValidating(false);
    }
  };

  const handleRun = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await api<any>('/requests/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: requestYaml }),
      });
      setNotification(`Infrastructure run started: ${res.request_id || 'Submitted'}`);
      setOpenNewDialog(false);
      await loadAll();
      if (res.request_id) {
        handleOpenDetail(res.request_id);
      }
    } catch (err: any) {
      setError(err.message || 'Pipeline execution failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedRequest) return;
    setApproving(true);
    try {
      const res = await api<{ token: string; approver: string }>(`/requests/${selectedRequest.id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approver: 'Portal User' }),
      });
      setApprovalToken(res.token);
      setNotification('Plan approved! Single-use deployment token issued.');
      await handleOpenDetail(selectedRequest.id);
    } catch (err: any) {
      setError(err.message || 'Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const handleApply = async () => {
    if (!selectedRequest || !approvalToken) return;
    setApplying(true);
    try {
      const res = await api<any>(`/requests/${selectedRequest.id}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: approvalToken }),
      });
      setNotification(`Deployment completed: ${res.status || 'Applied'}`);
      await handleOpenDetail(selectedRequest.id);
      await loadAll();
    } catch (err: any) {
      setError(err.message || 'Apply failed');
    } finally {
      setApplying(false);
    }
  };

  const filteredCatalog = Object.entries(catalog).filter(([name]) =>
    name.toLowerCase().includes(catalogSearch.toLowerCase())
  );

  return (
    <Page themeId="tool">
      <ContentHeader title="Infrastructure Agent">
        <Button
          variant="outlined"
          color="default"
          startIcon={<RefreshIcon />}
          onClick={loadAll}
          disabled={loading}
          style={{ marginRight: 8 }}
        >
          Refresh
        </Button>
        <Button
          variant="contained"
          color="primary"
          startIcon={<AddIcon />}
          onClick={() => {
            setValidationResult(null);
            setOpenNewDialog(true);
          }}
        >
          New Request
        </Button>
        <SupportButton>
          Azure Infrastructure Agent uses deterministic policy and LLM reasoning to generate and safely deploy Terraform infrastructure.
        </SupportButton>
      </ContentHeader>

      <Content>
        {/* Status Banner */}
        {status && (
          <Paper style={{ padding: 16, marginBottom: 20 }}>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} sm={3}>
                <Typography variant="subtitle2" color="textSecondary">Provider</Typography>
                <Typography variant="body1" style={{ fontWeight: 600 }}>
                  {status.activeProvider.toUpperCase()}
                  {status.isPlaceholder && (
                    <Chip label="Endpoint Config Needed" size="small" color="secondary" style={{ marginLeft: 8 }} />
                  )}
                </Typography>
              </Grid>
              <Grid item xs={12} sm={3}>
                <Typography variant="subtitle2" color="textSecondary">Approved Modules</Typography>
                <Typography variant="body1" style={{ fontWeight: 600 }}>
                  {status.moduleCount} Azure Modules Ready
                </Typography>
              </Grid>
              <Grid item xs={12} sm={3}>
                <Typography variant="subtitle2" color="textSecondary">Allowed Locations</Typography>
                <Typography variant="body2">
                  {status.policy?.allowed_locations?.join(', ') || 'Any'}
                </Typography>
              </Grid>
              <Grid item xs={12} sm={3}>
                <Typography variant="subtitle2" color="textSecondary">Policy Limits</Typography>
                <Typography variant="body2">
                  Max calls: {status.policy?.max_module_calls || 25} | Max changes: {status.policy?.max_resource_changes || 100}
                </Typography>
              </Grid>
            </Grid>
          </Paper>
        )}

        {error && (
          <Paper style={{ padding: 12, marginBottom: 16, backgroundColor: '#ffebee', color: '#c62828' }}>
            <Typography variant="body2">{error}</Typography>
          </Paper>
        )}

        {notification && (
          <Paper style={{ padding: 12, marginBottom: 16, backgroundColor: '#e8f5e9', color: '#2e7d32' }}>
            <Typography variant="body2">{notification}</Typography>
          </Paper>
        )}

        <Tabs value={tab} onChange={(_, val) => setTab(val)} indicatorColor="primary" textColor="primary" style={{ marginBottom: 16 }}>
          <Tab label={`Requests (${requests.length})`} />
          <Tab label={`Module Catalog (${Object.keys(catalog).length})`} />
        </Tabs>

        {/* Tab 0: Requests List */}
        {tab === 0 && (
          <Paper>
            <TableContainer>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableCell>Request ID</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Description</TableCell>
                    <TableCell>Environment</TableCell>
                    <TableCell>Location</TableCell>
                    <TableCell>Plan Summary</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {requests.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} align="center" style={{ padding: 32 }}>
                        {loading ? <CircularProgress size={28} /> : (
                          <Typography variant="body2" color="textSecondary">
                            No infrastructure requests yet. Click "New Request" to create one.
                          </Typography>
                        )}
                      </TableCell>
                    </TableRow>
                  ) : (
                    requests.map(req => {
                      const counts = req.planSummary?.counts || {};
                      const countStr = [
                        counts.add ? `+${counts.add}` : '',
                        counts.change ? `~${counts.change}` : '',
                        counts.destroy ? `-${counts.destroy}` : '',
                      ].filter(Boolean).join(' ') || 'None';

                      return (
                        <TableRow key={req.id} hover>
                          <TableCell style={{ fontFamily: 'monospace', fontWeight: 600 }}>{req.id}</TableCell>
                          <TableCell>
                            <Box display="flex" alignItems="center">
                              <StatusIcon status={req.status} />
                              <Chip label={req.status} size="small" color={statusColor(req.status)} />
                            </Box>
                          </TableCell>
                          <TableCell style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {req.description || '—'}
                          </TableCell>
                          <TableCell>{req.environment || '—'}</TableCell>
                          <TableCell>{req.location || '—'}</TableCell>
                          <TableCell style={{ fontFamily: 'monospace' }}>{countStr}</TableCell>
                          <TableCell align="right">
                            <Button
                              variant="outlined"
                              size="small"
                              onClick={() => handleOpenDetail(req.id)}
                            >
                              Inspect
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        )}

        {/* Tab 1: Module Catalog */}
        {tab === 1 && (
          <Paper style={{ padding: 16 }}>
            <Box display="flex" alignItems="center" marginBottom={2}>
              <SearchIcon style={{ marginRight: 8, color: '#888' }} />
              <TextField
                placeholder="Search approved Terraform modules..."
                value={catalogSearch}
                onChange={e => setCatalogSearch(e.target.value)}
                variant="outlined"
                size="small"
                fullWidth
              />
            </Box>

            <Grid container spacing={2}>
              {filteredCatalog.map(([modName, mod]) => (
                <Grid item xs={12} sm={6} md={4} key={modName}>
                  <Paper variant="outlined" style={{ padding: 14, height: '100%' }}>
                    <Box display="flex" justifyContent="space-between" alignItems="center">
                      <Typography variant="subtitle1" style={{ fontWeight: 600 }}>
                        {modName}
                      </Typography>
                      <Chip
                        label={mod.enabled ? 'Approved' : 'Disabled'}
                        size="small"
                        color={mod.enabled ? 'primary' : 'default'}
                      />
                    </Box>

                    <Box marginTop={1}>
                      <Typography variant="caption" color="textSecondary" display="block">
                        Required Inputs ({mod.required?.length || 0}):
                      </Typography>
                      <Typography variant="body2" style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                        {mod.required?.length > 0 ? mod.required.join(', ') : 'None'}
                      </Typography>
                    </Box>

                    <Box marginTop={1}>
                      <Typography variant="caption" color="textSecondary" display="block">
                        Outputs ({mod.outputs?.length || 0}):
                      </Typography>
                      <Typography variant="body2" style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                        {mod.outputs?.length > 0 ? mod.outputs.slice(0, 5).join(', ') + (mod.outputs.length > 5 ? '…' : '') : 'None'}
                      </Typography>
                    </Box>
                  </Paper>
                </Grid>
              ))}
            </Grid>
          </Paper>
        )}

        {/* New Request Modal */}
        <Dialog open={openNewDialog} onClose={() => setOpenNewDialog(false)} maxWidth="md" fullWidth>
          <DialogTitle>Create Infrastructure Request</DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="textSecondary" paragraph>
              Specify your infrastructure requirements in YAML or JSON. The agent will parse your requirements, match approved catalog modules, generate Terraform configuration, perform static security checks, and compute an execution plan.
            </Typography>

            <TextField
              multiline
              rows={14}
              variant="outlined"
              fullWidth
              value={requestYaml}
              onChange={e => setRequestYaml(e.target.value)}
              style={{ fontFamily: 'monospace', fontSize: '0.875rem' }}
            />

            {validationResult && (
              <Box marginTop={2}>
                {validationResult.valid ? (
                  <Chip
                    icon={<CheckCircleIcon />}
                    label={validationResult.message}
                    color="primary"
                  />
                ) : (
                  <Paper style={{ padding: 8, backgroundColor: '#ffebee', color: '#c62828' }}>
                    <Typography variant="body2" style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
                      {validationResult.error}
                    </Typography>
                  </Paper>
                )}
              </Box>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setOpenNewDialog(false)} color="default">
              Cancel
            </Button>
            <Button
              onClick={handleValidate}
              color="default"
              disabled={validating || submitting}
            >
              {validating ? <CircularProgress size={20} /> : 'Validate Schema'}
            </Button>
            <Button
              onClick={handleRun}
              color="primary"
              variant="contained"
              disabled={submitting}
            >
              {submitting ? <CircularProgress size={20} color="inherit" /> : 'Run Infra Agent'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Request Inspection Modal */}
        <Dialog
          open={Boolean(selectedRequestId)}
          onClose={() => setSelectedRequestId(null)}
          maxWidth="lg"
          fullWidth
        >
          <DialogTitle>
            <Box display="flex" justifyContent="space-between" alignItems="center">
              <Box display="flex" alignItems="center">
                <CodeIcon style={{ marginRight: 8 }} />
                <Typography variant="h6">Request: {selectedRequestId}</Typography>
              </Box>
              {selectedRequest?.state?.status && (
                <Chip
                  label={selectedRequest.state.status}
                  color={statusColor(selectedRequest.state.status)}
                />
              )}
            </Box>
          </DialogTitle>

          <DialogContent dividers>
            {loadingDetail ? (
              <Box display="flex" justifyContent="center" padding={4}>
                <CircularProgress />
              </Box>
            ) : selectedRequest ? (
              <Grid container spacing={3}>
                {/* Status & Approval Gate */}
                <Grid item xs={12}>
                  <Paper variant="outlined" style={{ padding: 16, backgroundColor: '#fafafa' }}>
                    <Typography variant="subtitle1" style={{ fontWeight: 600 }}>
                      Status: {selectedRequest.state?.status_message || selectedRequest.state?.status}
                    </Typography>

                    {selectedRequest.state?.status === 'AWAITING_APPROVAL' && (
                      <Box marginTop={2}>
                        <Typography variant="body2" paragraph>
                          The Terraform plan has been calculated and passed all policy constraints. An explicit human approval is required before applying.
                        </Typography>

                        {!approvalToken ? (
                          <Button
                            variant="contained"
                            color="secondary"
                            startIcon={<VerifiedUserIcon />}
                            onClick={handleApprove}
                            disabled={approving}
                          >
                            {approving ? <CircularProgress size={20} color="inherit" /> : 'Issue Human Approval Token'}
                          </Button>
                        ) : (
                          <Box>
                            <Paper style={{ padding: 12, backgroundColor: '#e8f5e9', marginBottom: 12 }}>
                              <Typography variant="caption" color="textSecondary" display="block">
                                Approval Token (Single-Use, Bound to Plan Hash):
                              </Typography>
                              <Typography variant="body2" style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
                                {approvalToken}
                              </Typography>
                            </Paper>
                            <Button
                              variant="contained"
                              color="primary"
                              startIcon={<PlayArrowIcon />}
                              onClick={handleApply}
                              disabled={applying}
                            >
                              {applying ? <CircularProgress size={20} color="inherit" /> : 'Apply Terraform Plan'}
                            </Button>
                          </Box>
                        )}
                      </Box>
                    )}

                    {selectedRequest.result?.outputs && (
                      <Box marginTop={2}>
                        <Typography variant="subtitle2">Deployment Outputs:</Typography>
                        <pre style={{ backgroundColor: '#fff', padding: 8, borderRadius: 4, overflow: 'auto' }}>
                          {JSON.stringify(selectedRequest.result.outputs, null, 2)}
                        </pre>
                      </Box>
                    )}
                  </Paper>
                </Grid>

                {/* Plan Summary */}
                {selectedRequest.planSummary && (
                  <Grid item xs={12}>
                    <Typography variant="h6" gutterBottom>Plan Summary</Typography>
                    <Box display="flex" style={{ gap: 12, marginBottom: 8 }}>
                      <Chip label={`Add: ${selectedRequest.planSummary.counts?.add || 0}`} size="small" />
                      <Chip label={`Change: ${selectedRequest.planSummary.counts?.change || 0}`} size="small" />
                      <Chip label={`Destroy: ${selectedRequest.planSummary.counts?.destroy || 0}`} size="small" />
                    </Box>
                    <TableContainer component={Paper} variant="outlined" style={{ maxHeight: 200 }}>
                      <Table size="small">
                        <TableHead>
                          <TableRow>
                            <TableCell>Action</TableCell>
                            <TableCell>Resource Address</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {selectedRequest.planSummary.changes?.map((ch, idx) => (
                            <TableRow key={idx}>
                              <TableCell style={{ fontWeight: 600 }}>{ch.action}</TableCell>
                              <TableCell style={{ fontFamily: 'monospace' }}>{ch.address}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  </Grid>
                )}

                {/* Generated Terraform Files */}
                {selectedRequest.tfFiles && Object.keys(selectedRequest.tfFiles).length > 0 && (
                  <Grid item xs={12}>
                    <Typography variant="h6" gutterBottom>Generated Terraform Files</Typography>
                    <Box display="flex" style={{ gap: 8, marginBottom: 8 }}>
                      {Object.keys(selectedRequest.tfFiles).map(f => (
                        <Chip
                          key={f}
                          label={f}
                          clickable
                          color={selectedTfTab === f ? 'primary' : 'default'}
                          onClick={() => setSelectedTfTab(f)}
                        />
                      ))}
                    </Box>
                    <Paper
                      variant="outlined"
                      style={{
                        padding: 12,
                        backgroundColor: '#263238',
                        color: '#eceff1',
                        maxHeight: 280,
                        overflow: 'auto',
                      }}
                    >
                      <pre style={{ margin: 0, fontFamily: 'monospace', fontSize: '0.85rem' }}>
                        {selectedRequest.tfFiles[selectedTfTab] || 'No content'}
                      </pre>
                    </Paper>
                  </Grid>
                )}

                {/* Audit Trail */}
                {selectedRequest.auditLogs && selectedRequest.auditLogs.length > 0 && (
                  <Grid item xs={12}>
                    <Accordion variant="outlined">
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                        <Typography variant="subtitle2">Audit Trail ({selectedRequest.auditLogs.length} events)</Typography>
                      </AccordionSummary>
                      <AccordionDetails>
                        <Box width="100%">
                          {selectedRequest.auditLogs.map((log, idx) => (
                            <Box key={idx} paddingY={0.5} borderBottom="1px solid #eee">
                              <Typography variant="caption" style={{ fontFamily: 'monospace' }}>
                                [{log.ts || log.timestamp || idx}] <strong>{log.event || log.action || 'event'}</strong>: {JSON.stringify(log)}
                              </Typography>
                            </Box>
                          ))}
                        </Box>
                      </AccordionDetails>
                    </Accordion>
                  </Grid>
                )}
              </Grid>
            ) : null}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setSelectedRequestId(null)} color="primary">
              Close
            </Button>
          </DialogActions>
        </Dialog>
      </Content>
    </Page>
  );
}

const infraAgentPage = PageBlueprint.make({
  name: 'infra-agent',
  params: {
    path: '/infra-agent',
    title: 'Infra Agent',
    icon: <LayersIcon />,
    loader: async () => <InfraAgentPage />,
  },
});

export const infraAgentModule = createFrontendModule({
  pluginId: 'app',
  extensions: [infraAgentPage],
});
