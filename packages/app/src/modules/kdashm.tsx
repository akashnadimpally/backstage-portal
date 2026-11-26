import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { discoveryApiRef, fetchApiRef, useApi } from '@backstage/core-plugin-api';
import { Page, Content } from '@backstage/core-components';
import DashboardIcon from '@material-ui/icons/Dashboard';
import KDashMHome from './kdashm-home';
import { configureKDashMFetch, resetKDashMFetch } from './kdashm-api';

const KDashMPage = () => {
  const hostRef = useRef<HTMLDivElement>(null);
  const [shadowRoot, setShadowRoot] = useState<ShadowRoot>();
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const root = host.attachShadow({ mode: 'open' });
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = '/kdashm.css';
    root.appendChild(stylesheet);

    const overrides = document.createElement('style');
    overrides.textContent = `
      :host {
        --background: #0a0a0c;
        --foreground: #f8fafc;
        --card-bg: rgba(30, 30, 35, 0.7);
        --card-border: rgba(255, 255, 255, 0.1);
        --primary: #3b82f6;
        --secondary: #6366f1;
        --accent: #8b5cf6;
        --success: #22c55e;
        --warning: #f59e0b;
        --danger: #ef4444;
        --glass-bg: rgba(255, 255, 255, 0.03);
        --glass-border: rgba(255, 255, 255, 0.08);
        --sidebar-width: 260px;
        display: block;
        height: 100%;
        width: 100%;
        color: var(--foreground);
        font-family: 'Segoe UI', sans-serif;
        background: var(--background);
      }
      #kdashm-root { height: 100%; width: 100%; }
      #kdashm-root .sidebar { height: 100%; }
      #kdashm-root .main-content { height: 100%; min-width: 0; overflow-y: auto; padding: 1.25rem; }
    `;
    root.appendChild(overrides);
    configureKDashMFetch(fetchApi, discoveryApi);
    setShadowRoot(root);

    return () => {
      resetKDashMFetch();
      setShadowRoot(undefined);
    };
  }, [discoveryApi, fetchApi]);

  return (
    <Page themeId="home">
      <Content noPadding>
        <div ref={hostRef} style={{ height: 'calc(100vh - 48px)', minHeight: 480, width: '100%' }}>
          {shadowRoot && createPortal(<KDashMHome />, shadowRoot)}
        </div>
      </Content>
    </Page>
  );
};

const kdashmPage = PageBlueprint.make({
  name: 'kdashm',
  params: {
    path: '/kdashm',
    title: 'Kubernetes Dashboard',
    icon: <DashboardIcon />,
    loader: async () => <KDashMPage />,
  },
});

export const kdashmModule = createFrontendModule({
  pluginId: 'app',
  extensions: [kdashmPage],
});