import { createApp } from '@backstage/frontend-defaults';
import catalogPlugin from '@backstage/plugin-catalog/alpha';
import { navModule } from './modules/nav';
import { authModule } from './modules/auth';
import { homeModule } from './modules/home';
import { portalHomeModule } from './modules/portal-home';
import { kdashmModule } from './modules/kdashm';
import { cloudModule } from './modules/cloud';
import { localUsersModule } from './modules/local-users';
import { pipelinesModule } from './modules/pipelines';
import { infraAgentModule } from './modules/infra-agent';
import kubernetesPlugin from '@backstage/plugin-kubernetes/alpha';
import rbacPlugin from '@backstage-community/plugin-rbac';

export default createApp({
  features: [catalogPlugin, kubernetesPlugin, rbacPlugin, navModule, authModule, homeModule, portalHomeModule, kdashmModule, cloudModule, localUsersModule, pipelinesModule, infraAgentModule],
});




