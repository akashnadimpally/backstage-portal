import { useEffect, useState } from 'react';
import {
  Button,
  CircularProgress,
  Container,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@material-ui/core';
import PeopleIcon from '@material-ui/icons/People';
import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { discoveryApiRef, fetchApiRef, identityApiRef, useApi } from '@backstage/core-plugin-api';
import { Content, Page } from '@backstage/core-components';

type LocalUser = {
  email: string;
  entity_ref: string;
  created_at: string;
};

function LocalUsersPage() {
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);
  const identityApi = useApi(identityApiRef);
  const [isAdmin, setIsAdmin] = useState(false);
  const [users, setUsers] = useState<LocalUser[]>([]);
  const [email, setEmail] = useState('');
  const [entityRef, setEntityRef] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const postAction = async (body: Record<string, string>) => {
    const baseUrl = await discoveryApi.getBaseUrl('auth');
    return fetchApi.fetch(`${baseUrl}/local/handler/frame`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  };

  const loadUsers = async () => {
    const response = await postAction({ action: 'list-users' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to load accounts');
    setUsers(result.users);
  };

  useEffect(() => {
    void identityApi.getBackstageIdentity().then(identity => {
      const admin = identity.userEntityRef.toLowerCase() === 'user:default/admin';
      setIsAdmin(admin);
      if (admin) void loadUsers().catch(error => setMessage(error.message));
    });
  }, [identityApi]);

  const createUser = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      const response = await postAction({ action: 'create-user', email, entityRef, password });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to create account');
      setEmail('');
      setEntityRef('');
      setPassword('');
      setMessage('Account created. Assign its role in RBAC.');
      await loadUsers();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to create account');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page themeId="home">
      <Content>
        <Container maxWidth="md">
          <Typography variant="h4" gutterBottom>Local accounts</Typography>
          {!isAdmin ? (
            <Typography color="error">Administrator access required.</Typography>
          ) : (
            <>
              <Paper style={{ padding: 24, marginBottom: 24 }}>
                <Typography variant="h6" gutterBottom>Create account</Typography>
                <form onSubmit={createUser}>
                  <TextField fullWidth required type="email" label="Email" value={email} onChange={event => setEmail(event.target.value)} margin="normal" />
                  <TextField fullWidth required label="Catalog user reference" placeholder="user:default/alex" value={entityRef} onChange={event => setEntityRef(event.target.value)} margin="normal" />
                  <TextField fullWidth required type="password" label="Password (12+ characters)" value={password} onChange={event => setPassword(event.target.value)} margin="normal" inputProps={{ minLength: 12, maxLength: 1024, autoComplete: 'new-password' }} />
                  <Button color="primary" disabled={busy} type="submit" variant="contained" style={{ marginTop: 16 }}>
                    {busy ? <CircularProgress size={22} /> : 'Create account'}
                  </Button>
                </form>
                {message && <Typography role="status" style={{ marginTop: 16 }}>{message}</Typography>}
              </Paper>
              <Paper>
                <Table size="small">
                  <TableHead><TableRow><TableCell>Email</TableCell><TableCell>Catalog user</TableCell></TableRow></TableHead>
                  <TableBody>
                    {users.map(user => (
                      <TableRow key={user.entity_ref}>
                        <TableCell>{user.email}</TableCell>
                        <TableCell>{user.entity_ref}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Paper>
            </>
          )}
        </Container>
      </Content>
    </Page>
  );
}

const localUsersPage = PageBlueprint.make({
  name: 'local-users',
  params: {
    path: '/user-admin',
    title: 'Local accounts',
    icon: <PeopleIcon />,
    loader: async () => <LocalUsersPage />,
  },
});

export const localUsersModule = createFrontendModule({
  pluginId: 'app',
  extensions: [localUsersPage],
});