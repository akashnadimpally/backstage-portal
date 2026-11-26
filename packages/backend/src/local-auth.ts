import { randomBytes, scrypt as scryptCallback, timingSafeEqual } from 'crypto';
import { promisify } from 'util';
import { coreServices, createBackendModule } from '@backstage/backend-plugin-api';
import { authProvidersExtensionPoint } from '@backstage/plugin-auth-node';

const scrypt = promisify(scryptCallback);
const passwordKeyLength = 64;
const adminEntityRef = 'user:default/admin';
const failedLogins = new Map<string, { count: number; resetAt: number }>();

type LocalUser = {
  email: string;
  entity_ref: string;
  salt: string;
  password_hash: string;
};

function normalizeEmail(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const email = value.trim().toLowerCase();
  return email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : undefined;
}

async function hashPassword(password: string, salt = randomBytes(16)) {
  const derivedKey = await scrypt(password, salt, passwordKeyLength) as Buffer;
  return { salt: salt.toString('base64'), passwordHash: derivedKey.toString('base64') };
}

export const localAuthModule = createBackendModule({
  pluginId: 'auth',
  moduleId: 'local-credentials-provider',
  register(reg) {
    reg.registerInit({
      deps: {
        providers: authProvidersExtensionPoint,
        database: coreServices.database,
        httpAuth: coreServices.httpAuth,
      },
      async init({ providers, database, httpAuth }) {
        const db = await database.getClient();
        if (!(await db.schema.hasTable('local_auth_users'))) {
          try {
            await db.schema.createTable('local_auth_users', table => {
              table.string('email', 254).primary();
              table.string('entity_ref', 255).notNullable().unique();
              table.string('salt', 64).notNullable();
              table.string('password_hash', 128).notNullable();
              table.timestamp('created_at').notNullable().defaultTo(db.fn.now());
            });
          } catch (error) {
            if (!(await db.schema.hasTable('local_auth_users'))) throw error;
          }
        }

        const adminEmail = 'admin@portal.dev.skynet';
        const adminPassword = process.env.LOCAL_AUTH_ADMIN_PASSWORD;
        if (adminEmail && adminPassword && adminPassword.length >= 12 && adminPassword.length <= 1024) {
          const existingAdmin = await db<LocalUser>('local_auth_users').where({ email: adminEmail }).first();
          if (!existingAdmin) {
            const hashed = await hashPassword(adminPassword);
            try {
              await db('local_auth_users').insert({
                email: adminEmail,
                entity_ref: adminEntityRef,
                salt: hashed.salt,
                password_hash: hashed.passwordHash,
              });
            } catch (error) {
              if (!(await db<LocalUser>('local_auth_users').where({ email: adminEmail }).first())) throw error;
            }
          }
        } else {
          console.warn('Local auth admin account was not bootstrapped; set LOCAL_AUTH_ADMIN_PASSWORD to a 12-1024 character secret.');
        }

        providers.registerProvider({
          providerId: 'local',
          factory: ({ resolverContext }) => ({
            async start(_req, res) {
              res.status(405).json({ error: 'Use the email and password sign-in form.' });
            },
            async frameHandler(req, res) {
              const { action } = req.body ?? {};

              if (action === 'login') {
                const email = normalizeEmail(req.body?.email);
                const password = req.body?.password;
                if (!email || typeof password !== 'string' || password.length === 0 || password.length > 1024) {
                  return res.status(401).json({ error: 'Invalid email or password' });
                }

                const attemptKey = `${req.ip}:${email}`;
                const now = Date.now();
                const attempt = failedLogins.get(attemptKey);
                if (attempt && attempt.resetAt > now && attempt.count >= 5) {
                  return res.status(429).json({ error: 'Too many sign-in attempts. Try again later.' });
                }

                const account = await db<LocalUser>('local_auth_users').where({ email }).first();
                const salt = account ? Buffer.from(account.salt, 'base64') : Buffer.alloc(16);
                const actual = await scrypt(password, salt, passwordKeyLength) as Buffer;
                const expected = account ? Buffer.from(account.password_hash, 'base64') : Buffer.alloc(passwordKeyLength);
                const valid = Boolean(account && expected.length === actual.length && timingSafeEqual(expected, actual));
                if (!valid || !account) {
                  failedLogins.set(attemptKey, {
                    count: attempt && attempt.resetAt > now ? attempt.count + 1 : 1,
                    resetAt: attempt && attempt.resetAt > now ? attempt.resetAt : now + 15 * 60 * 1000,
                  });
                  return res.status(401).json({ error: 'Invalid email or password' });
                }

                failedLogins.delete(attemptKey);
                try {
                  const { entity } = await resolverContext.findCatalogUser({ entityRef: account.entity_ref });
                  const profile = entity.spec?.profile as { email?: string } | undefined;
                  if (normalizeEmail(profile?.email) !== account.email) {
                    return res.status(403).json({ error: 'This account does not match the catalog user email.' });
                  }
                  const backstageIdentity = await resolverContext.signInWithCatalogUser({
                    entityRef: account.entity_ref,
                  });
                  return res.json({
                    profile: { email: account.email },
                    backstageIdentity,
                  });
                } catch {
                  return res.status(403).json({ error: 'This account is not linked to an active Backstage catalog user.' });
                }
              }

              let credentials;
              try {
                credentials = await httpAuth.credentials(req, { allow: ['user'] });
              } catch {
                return res.status(401).json({ error: 'Backstage authentication is required' });
              }
              if (credentials.principal.userEntityRef !== adminEntityRef) {
                return res.status(403).json({ error: 'Only the portal administrator can manage local accounts.' });
              }

              if (action === 'list-users') {
                const users = await db<LocalUser>('local_auth_users')
                  .select('email', 'entity_ref', 'created_at')
                  .orderBy('email');
                return res.json({ users });
              }

              if (action === 'create-user') {
                const email = normalizeEmail(req.body?.email);
                const entityRef = typeof req.body?.entityRef === 'string' ? req.body.entityRef.trim() : '';
                const password = req.body?.password;
                if (!email || !/^user:[a-z0-9-]+\/[a-z0-9][a-z0-9._-]*$/i.test(entityRef) ||
                    typeof password !== 'string' || password.length < 12 || password.length > 1024) {
                  return res.status(400).json({ error: 'A valid email, user entity reference, and password of at least 12 characters are required.' });
                }

                let catalogEmail: string | undefined;
                try {
                  const { entity } = await resolverContext.findCatalogUser({ entityRef });
                  const profile = entity.spec?.profile as { email?: string } | undefined;
                  catalogEmail = normalizeEmail(profile?.email);
                } catch {
                  return res.status(400).json({ error: 'The user entity must exist in the Backstage catalog.' });
                }
                if (catalogEmail !== email) {
                  return res.status(400).json({ error: 'The account email must match the catalog user profile email.' });
                }

                const hashed = await hashPassword(password);
                try {
                  await db('local_auth_users').insert({
                    email,
                    entity_ref: entityRef,
                    salt: hashed.salt,
                    password_hash: hashed.passwordHash,
                  });
                  return res.status(201).json({ success: true });
                } catch {
                  return res.status(409).json({ error: 'An account already exists for this email or catalog user.' });
                }
              }

              return res.status(400).json({ error: 'Unsupported local auth action' });
            },
            async refresh(_req, res) {
              res.status(401).json({ error: 'Sign in again to continue.' });
            },
            async logout(_req, res) {
              res.status(204).end();
            },
          }),
        });
      },
    });
  },
});

export default localAuthModule;