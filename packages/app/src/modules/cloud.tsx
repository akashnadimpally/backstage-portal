import React, { useState, useEffect, useRef } from 'react';
import {
  createFrontendModule,
  PageBlueprint,
} from '@backstage/frontend-plugin-api';
import {
  discoveryApiRef,
  fetchApiRef,
  useApi,
} from '@backstage/core-plugin-api';
import {
  Header,
  HeaderLabel,
  Page,
  Content,
  ContentHeader,
  InfoCard,
  Table,
  TableColumn,
} from '@backstage/core-components';
import {
  Box,
  Typography,
  Tabs,
  Tab,
  Button,
  Grid,
  Card,
  CardContent,
  Chip,
  Paper,
  CircularProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Divider,
} from '@material-ui/core';
import CloudIcon from '@material-ui/icons/Cloud';
import LockIcon from '@material-ui/icons/Lock';
import CheckCircleIcon from '@material-ui/icons/CheckCircle';
import AttachMoneyIcon from '@material-ui/icons/AttachMoney';
import StorageIcon from '@material-ui/icons/Storage';

interface Subscription {
  id: string;
  name: string;
  state: string;
  tenantDisplayName: string;
  user: string;
  isDefault: boolean;
  currentCost: string;
  forecastCost: string;
  billingPeriodStartDate: string;
  billingPeriodEndDate: string;
}

interface Resource {
  id: string;
  name: string;
  type: string;
  location: string;
  resourceGroup: string;
  provisioningState: string;
}

interface ResourceGroup {
  id: string;
  name: string;
  location: string;
}

const CloudAzurePage = () => {
  const fetchApi = useApi(fetchApiRef);
  const discoveryApi = useApi(discoveryApiRef);
  const [activeTab, setActiveTab] = useState<'subscriptions' | 'resources'>(
    'subscriptions',
  );
  const [authenticated, setAuthenticated] = useState<boolean>(false);
  const [authUser, setAuthUser] = useState<string>('');
  const [loadingAuth, setLoadingAuth] = useState<boolean>(false);
  const [verificationUri, setVerificationUri] = useState<string>('');
  const [userCode, setUserCode] = useState<string>('');
  const [loginError, setLoginError] = useState<string>('');
  const loginPollRef = useRef<number | null>(null);

  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [selectedSubId, setSelectedSubId] = useState<string>('');
  const [resources, setResources] = useState<Resource[]>([]);
  const [resourceGroups, setResourceGroups] = useState<ResourceGroup[]>([]);
  const [loadingResources, setLoadingResources] = useState<boolean>(false);

  const fetchCloudApi = async (path: string, init?: RequestInit) => {
    const baseUrl = await discoveryApi.getBaseUrl('cloud');
    return fetchApi.fetch(`${baseUrl}/azure/${path}`, init);
  };

  // Check authentication status on mount
  const checkAuthStatus = async () => {
    try {
      const res = await fetchCloudApi('status');
      const contentType = res.headers.get('content-type');
      if (res.ok && contentType && contentType.includes('application/json')) {
        const data = await res.json();
        if (data.authenticated) {
          setAuthenticated(true);
          setAuthUser(data.user);
          setLoginError('');
          loadSubscriptions();
        }
      }
    } catch {
      // Fallback handle
    }
  };

  // Load all Azure subscriptions
  const loadSubscriptions = async () => {
    try {
      const res = await fetchCloudApi('subscriptions');
      const contentType = res.headers.get('content-type');
      if (res.ok && contentType && contentType.includes('application/json')) {
        const data = await res.json();
        setSubscriptions(data.subscriptions || []);
        if (data.subscriptions && data.subscriptions.length > 0) {
          const defaultSub =
            data.subscriptions.find((s: Subscription) => s.isDefault) ||
            data.subscriptions[0];
          setSelectedSubId(defaultSub.id);
        }
      }
    } catch {
      // Fallback handle
    }
  };

  // Fetch resources when selected subscription changes
  const loadResources = async (subId: string) => {
    if (!subId) return;
    setLoadingResources(true);
    try {
      const params = new URLSearchParams({ subscriptionId: subId });
      const res = await fetchCloudApi(`resources?${params}`);
      const contentType = res.headers.get('content-type');
      if (res.ok && contentType && contentType.includes('application/json')) {
        const data = await res.json();
        setResources(data.resources || []);
      }
    } catch {
      setResources([]);
    } finally {
      setLoadingResources(false);
    }
  };

  const loadResourceGroups = async (subId: string) => {
    if (!subId) return;
    try {
      const params = new URLSearchParams({ subscriptionId: subId });
      const res = await fetchCloudApi(`resource-groups?${params}`);
      if (res.ok) {
        const data = await res.json();
        setResourceGroups(data.resourceGroups || []);
      } else {
        setResourceGroups([]);
      }
    } catch {
      setResourceGroups([]);
    }
  };

  useEffect(() => {
    checkAuthStatus();
    return () => {
      if (loginPollRef.current !== null) {
        window.clearInterval(loginPollRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (selectedSubId && activeTab === 'resources') {
      loadResources(selectedSubId);
      loadResourceGroups(selectedSubId);
    }
  }, [selectedSubId, activeTab]);

  const handleLogin = async () => {
    setLoadingAuth(true);
    setLoginError('');
    setUserCode('');
    try {
      const loginResponse = await fetchCloudApi('login', {
        method: 'POST',
      });
      const loginData = await loginResponse.json();
      if (!loginResponse.ok) {
        throw new Error(loginData.error || 'Unable to start Microsoft sign-in');
      }
      setVerificationUri(loginData.verificationUri);
      setUserCode(loginData.userCode);

      const startedAt = Date.now();
      loginPollRef.current = window.setInterval(async () => {
        try {
          const res = await fetchCloudApi('status');
          const contentType = res.headers.get('content-type');
          if (
            res.ok &&
            contentType &&
            contentType.includes('application/json')
          ) {
            const data = await res.json();
            if (data.authenticated) {
              if (loginPollRef.current !== null) {
                window.clearInterval(loginPollRef.current);
                loginPollRef.current = null;
              }
              setAuthenticated(true);
              setAuthUser(data.user);
              setLoadingAuth(false);
              setVerificationUri('');
              setUserCode('');
              loadSubscriptions();
            } else if (Date.now() - startedAt > 600000) {
              if (loginPollRef.current !== null) {
                window.clearInterval(loginPollRef.current);
                loginPollRef.current = null;
              }
              setLoadingAuth(false);
              setLoginError(
                'Microsoft sign-in timed out. Start again to get a new code.',
              );
            }
          }
        } catch {
          // Polling fallback
        }
      }, 2000);
    } catch (error) {
      setLoadingAuth(false);
      setLoginError(
        error instanceof Error
          ? error.message
          : 'Unable to start Microsoft sign-in',
      );
    }
  };

  const subscriptionColumns: TableColumn<Subscription>[] = [
    { title: 'Subscription Name', field: 'name', highlight: true },
    { title: 'Subscription ID', field: 'id' },
    { title: 'Directory / Tenant', field: 'tenantDisplayName' },
    { title: 'Account User', field: 'user' },
    {
      title: 'State',
      field: 'state',
      render: row => (
        <Chip
          size="small"
          label={row.state}
          style={{
            backgroundColor: row.state === 'Enabled' ? '#e8f5e9' : '#ffebee',
            color: row.state === 'Enabled' ? '#2e7d32' : '#c62828',
          }}
        />
      ),
    },
    { title: 'Month-to-date Cost', field: 'currentCost' },
    { title: 'Forecasted Month-end Cost', field: 'forecastCost' },
    { title: 'Billing Period Start', field: 'billingPeriodStartDate' },
    { title: 'Billing Period End', field: 'billingPeriodEndDate' },
  ];

  const resourceGroupColumns: TableColumn<ResourceGroup>[] = [
    { title: 'Resource Group', field: 'name', highlight: true },
    { title: 'Location', field: 'location' },
    { title: 'Resource Group ID', field: 'id' },
  ];

  const resourceColumns: TableColumn<Resource>[] = [
    { title: 'Resource Name', field: 'name', highlight: true },
    { title: 'Resource Type', field: 'type' },
    { title: 'Location', field: 'location' },
    { title: 'Resource Group', field: 'resourceGroup' },
    {
      title: 'Status',
      field: 'provisioningState',
      render: row => (
        <Chip
          size="small"
          label={row.provisioningState || 'Active'}
          color="primary"
        />
      ),
    },
  ];

  return (
    <Page themeId="home">
      <Header
        title="Azure Cloud Portal"
        subtitle="Manage Azure Subscriptions, Cost Analysis & Provisioned Resources"
      >
        <HeaderLabel label="Cloud Provider" value="Microsoft Azure" />
        <HeaderLabel
          label="Status"
          value={authenticated ? 'Authenticated' : 'Login Required'}
        />
      </Header>
      <Content>
        {/* Authentication Top Banner */}
        <Paper style={{ padding: 20, marginBottom: 24 }}>
          <Box
            display="flex"
            alignItems="center"
            justifyContent="space-between"
          >
            <Box display="flex" alignItems="center" gridGap={16}>
              {authenticated ? (
                <CheckCircleIcon style={{ color: '#4caf50', fontSize: 36 }} />
              ) : (
                <LockIcon style={{ color: '#ff9800', fontSize: 36 }} />
              )}
              <Box>
                <Typography variant="h6">
                  Microsoft account authentication
                </Typography>
                <Typography variant="body2" color="textSecondary">
                  {authenticated
                    ? `Signed in as ${authUser} through Azure CLI`
                    : 'Sign in with your Microsoft account to view subscriptions, current costs, and resource groups.'}
                </Typography>
                {userCode && (
                  <Typography variant="body2" style={{ marginTop: 8 }}>
                    Open{' '}
                    <a href={verificationUri} target="_blank" rel="noreferrer">
                      Microsoft device login
                    </a>{' '}
                    and enter code <strong>{userCode}</strong>.
                  </Typography>
                )}
                {!authenticated && loginError && (
                  <Typography
                    variant="body2"
                    color="error"
                    style={{ marginTop: 8 }}
                  >
                    {loginError}
                  </Typography>
                )}
              </Box>
            </Box>
            <Box>
              {authenticated ? (
                <Chip label="Microsoft Account Authenticated" color="primary" />
              ) : (
                <Button
                  variant="contained"
                  color="primary"
                  onClick={handleLogin}
                  disabled={loadingAuth}
                  startIcon={
                    loadingAuth ? (
                      <CircularProgress size={20} color="inherit" />
                    ) : (
                      <LockIcon />
                    )
                  }
                >
                  {loadingAuth
                    ? 'Opening Microsoft Auth...'
                    : 'Login via Microsoft Account'}
                </Button>
              )}
            </Box>
          </Box>
        </Paper>

        {!authenticated ? (
          /* Lock Screen when not logged in */
          <Paper style={{ padding: 64, textAlign: 'center' }}>
            <LockIcon
              style={{ fontSize: 72, color: '#9e9e9e', marginBottom: 16 }}
            />
            <Typography variant="h5" gutterBottom>
              Authentication Required to View Azure Hub
            </Typography>
            <Typography
              variant="body1"
              color="textSecondary"
              style={{ maxWidth: 520, margin: '0 auto 24px' }}
            >
              Sign in with your Microsoft account to view the Azure
              subscriptions and resources available to you.
            </Typography>
            <Button
              variant="contained"
              color="primary"
              size="large"
              onClick={handleLogin}
              disabled={loadingAuth}
              startIcon={
                loadingAuth ? (
                  <CircularProgress size={20} color="inherit" />
                ) : (
                  <LockIcon />
                )
              }
            >
              Authenticate via Microsoft Account
            </Button>
          </Paper>
        ) : (
          /* Authenticated Azure Dashboard */
          <>
            <ContentHeader title="Azure Infrastructure & Cost Portal">
              <Paper variant="outlined">
                <Tabs
                  value={activeTab}
                  onChange={(_e, val) => setActiveTab(val)}
                  indicatorColor="primary"
                  textColor="primary"
                >
                  <Tab
                    value="subscriptions"
                    label="Subscriptions & Costs"
                    icon={<AttachMoneyIcon />}
                  />
                  <Tab
                    value="resources"
                    label="Resources Listing"
                    icon={<StorageIcon />}
                  />
                </Tabs>
              </Paper>
            </ContentHeader>

            <Divider style={{ marginBottom: 24 }} />

            {activeTab === 'subscriptions' ? (
              <Grid container spacing={3}>
                <Grid item xs={12}>
                  <Table
                    title="All Azure Subscriptions (Cost & Forecast)"
                    options={{ search: true, paging: true, pageSize: 5 }}
                    columns={subscriptionColumns}
                    data={subscriptions}
                  />
                  <Typography variant="caption" color="textSecondary">
                    Forecasts include month-to-date actual charges. Billing
                    dates are read from Azure's billing-period API and may be
                    unavailable for unsupported subscription types.
                  </Typography>
                </Grid>
              </Grid>
            ) : (
              <Grid container spacing={3}>
                <Grid item xs={12}>
                  <Paper style={{ padding: 20, marginBottom: 20 }}>
                    <Box display="flex" alignItems="center" gridGap={16}>
                      <FormControl variant="outlined" style={{ minWidth: 320 }}>
                        <InputLabel id="subscription-select-label">
                          Select Azure Subscription
                        </InputLabel>
                        <Select
                          labelId="subscription-select-label"
                          value={selectedSubId}
                          onChange={e =>
                            setSelectedSubId(e.target.value as string)
                          }
                          label="Select Azure Subscription"
                        >
                          {subscriptions.map(sub => (
                            <MenuItem key={sub.id} value={sub.id}>
                              {sub.name} ({sub.id})
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      {loadingResources && <CircularProgress size={24} />}
                    </Box>
                  </Paper>
                </Grid>

                <Grid item xs={12}>
                  <Table
                    title={`Resource Groups (${resourceGroups.length})`}
                    options={{ search: true, paging: true, pageSize: 10 }}
                    columns={resourceGroupColumns}
                    data={resourceGroups}
                  />
                </Grid>

                <Grid item xs={12}>
                  <Table
                    title={`Resources for Selected Subscription (${resources.length} found)`}
                    options={{ search: true, paging: true, pageSize: 10 }}
                    columns={resourceColumns}
                    data={resources}
                  />
                </Grid>
              </Grid>
            )}
          </>
        )}
      </Content>
    </Page>
  );
};

const cloudPage = PageBlueprint.make({
  name: 'cloud',
  params: {
    path: '/cloud',
    title: 'Cloud',
    icon: <CloudIcon />,
    loader: async () => <CloudAzurePage />,
  },
});

export const cloudModule = createFrontendModule({
  pluginId: 'app',
  extensions: [cloudPage],
});
