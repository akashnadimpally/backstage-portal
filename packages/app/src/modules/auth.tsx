import { useState } from 'react';
import { Button, CircularProgress, Container, Paper, TextField, Typography } from '@material-ui/core';
import { discoveryApiRef, type BackstageIdentityResponse, type IdentityApi, type ProfileInfo, useApi } from '@backstage/core-plugin-api';
import { SignInPageBlueprint, type SignInPageProps } from '@backstage/plugin-app-react';
import { createFrontendModule } from '@backstage/frontend-plugin-api';

type LocalSignInResponse = {
  profile: ProfileInfo;
  backstageIdentity: BackstageIdentityResponse;
};

function LocalSignInPage({ onSignInSuccess }: SignInPageProps) {
  const discoveryApi = useApi(discoveryApiRef);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const signIn = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    setLoading(true);
    try {
      const baseUrl = await discoveryApi.getBaseUrl('auth');
      const response = await fetch(`${baseUrl}/local/handler/frame`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'login', email, password }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Sign-in failed');

      const signInResult = result as LocalSignInResponse;
      const identityApi: IdentityApi = {
        getProfileInfo: async () => signInResult.profile,
        getBackstageIdentity: async () => signInResult.backstageIdentity.identity,
        getCredentials: async () => ({ token: signInResult.backstageIdentity.token }),
        signOut: async () => window.location.reload(),
      };
      onSignInSuccess(identityApi);
    } catch (signInError) {
      setError(signInError instanceof Error ? signInError.message : 'Sign-in failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="xs" style={{ paddingTop: '12vh' }}>
      <Paper elevation={4} style={{ padding: 32 }}>
        <Typography variant="h5" gutterBottom>Sign in</Typography>
        <form onSubmit={signIn}>
          <TextField
            autoComplete="username"
            autoFocus
            fullWidth
            label="Email"
            margin="normal"
            required
            type="email"
            value={email}
            onChange={event => setEmail(event.target.value)}
          />
          <TextField
            autoComplete="current-password"
            fullWidth
            label="Password"
            margin="normal"
            required
            type="password"
            value={password}
            onChange={event => setPassword(event.target.value)}
          />
          {error && <Typography color="error" role="alert">{error}</Typography>}
          <Button
            color="primary"
            disabled={loading}
            fullWidth
            type="submit"
            variant="contained"
            style={{ marginTop: 24 }}
          >
            {loading ? <CircularProgress size={22} /> : 'Sign in'}
          </Button>
        </form>
      </Paper>
    </Container>
  );
}

export const authModule = createFrontendModule({
  pluginId: 'app',
  extensions: [
    SignInPageBlueprint.make({
      params: {
        loader: async () => props => <LocalSignInPage {...props} />,
      },
    }),
  ],
});
