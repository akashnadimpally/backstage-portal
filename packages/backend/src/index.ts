/*
 * Hi!
 *
 * Note that this is an EXAMPLE Backstage backend. Please check the README.
 *
 * Happy hacking!
 */

import { createBackend } from '@backstage/backend-defaults';
import { coreServices, createBackendPlugin } from '@backstage/backend-plugin-api';
import { createCloudAuthRouter } from './cloud-auth';
import { createKDashMRouter, kdashmPermissions } from './kdashm-router';
import { createPipelinesRouter, pipelinesPermissions } from './pipelines-router';
import { createInfraAgentRouter, infraAgentPermissions } from './infra-agent-router';

const backend = createBackend();

backend.add(
  createBackendPlugin({
    pluginId: 'cloud',
    register(env) {
      env.registerInit({
        deps: {
          httpRouter: coreServices.httpRouter,
          httpAuth: coreServices.httpAuth,
        },
        async init({ httpRouter }) {
          httpRouter.use(createCloudAuthRouter());
        },
      });
    },
  }),
);

backend.add(
  createBackendPlugin({
    pluginId: 'kdashm',
    register(env) {
      env.registerInit({
        deps: {
          httpRouter: coreServices.httpRouter,
          httpAuth: coreServices.httpAuth,
          permissions: coreServices.permissions,
          permissionsRegistry: coreServices.permissionsRegistry,
        },
        async init({ httpRouter, httpAuth, permissions, permissionsRegistry }) {
          permissionsRegistry.addPermissions(kdashmPermissions);
          httpRouter.use(createKDashMRouter({ httpAuth, permissions }));
        },
      });
    },
  }),
);

backend.add(
  createBackendPlugin({
    pluginId: 'pipelines',
    register(env) {
      env.registerInit({
        deps: {
          httpRouter: coreServices.httpRouter,
          httpAuth: coreServices.httpAuth,
          permissions: coreServices.permissions,
          permissionsRegistry: coreServices.permissionsRegistry,
        },
        async init({ httpRouter, httpAuth, permissions, permissionsRegistry }) {
          permissionsRegistry.addPermissions(pipelinesPermissions);
          httpRouter.use(createPipelinesRouter({ httpAuth, permissions }));
        },
      });
    },
  }),
);

backend.add(
  createBackendPlugin({
    pluginId: 'infra-agent',
    register(env) {
      env.registerInit({
        deps: {
          httpRouter: coreServices.httpRouter,
          httpAuth: coreServices.httpAuth,
          permissions: coreServices.permissions,
          permissionsRegistry: coreServices.permissionsRegistry,
        },
        async init({ httpRouter, httpAuth, permissions, permissionsRegistry }) {
          permissionsRegistry.addPermissions(infraAgentPermissions);
          httpRouter.use(createInfraAgentRouter({ httpAuth, permissions }));
        },
      });
    },
  }),
);

backend.add(import('@backstage/plugin-app-backend'));
backend.add(import('@backstage/plugin-proxy-backend'));

// scaffolder plugin
backend.add(import('@backstage/plugin-scaffolder-backend'));
backend.add(import('@backstage/plugin-scaffolder-backend-module-github'));
backend.add(import('@backstage/plugin-scaffolder-backend-module-gitlab'));
backend.add(
  import('@backstage/plugin-scaffolder-backend-module-notifications'),
);

// techdocs plugin
backend.add(import('@backstage/plugin-techdocs-backend'));

// auth plugin
backend.add(import('@backstage/plugin-auth-backend'));
backend.add(import('./local-auth'));

// catalog plugin
backend.add(import('@backstage/plugin-catalog-backend'));
backend.add(
  import('@backstage/plugin-catalog-backend-module-scaffolder-entity-model'),
);
backend.add(import('@backstage/plugin-catalog-backend-module-github-org'));

// See https://backstage.io/docs/features/software-catalog/configuration#subscribing-to-catalog-errors
backend.add(import('@backstage/plugin-catalog-backend-module-logs'));

// permission plugin
backend.add(import('@backstage/plugin-permission-backend'));
backend.add(import('@backstage-community/plugin-rbac-backend'));

// search plugin
backend.add(import('@backstage/plugin-search-backend'));

// search engine
// See https://backstage.io/docs/features/search/search-engines
backend.add(import('@backstage/plugin-search-backend-module-pg'));

// search collators
backend.add(import('@backstage/plugin-search-backend-module-catalog'));
backend.add(import('@backstage/plugin-search-backend-module-techdocs'));

// kubernetes plugin
backend.add(import('@backstage/plugin-kubernetes-backend'));
backend.add(import('./kubernetes-aks-discovery'));

// user settings plugin
backend.add(import('@backstage/plugin-user-settings-backend'));

// notifications and signals plugins
backend.add(import('@backstage/plugin-notifications-backend'));
backend.add(import('@backstage/plugin-signals-backend'));

// mcp actions plugin
backend.add(import('@backstage/plugin-mcp-actions-backend'));

backend.start();
