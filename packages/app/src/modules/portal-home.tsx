import { createFrontendModule, PageBlueprint } from '@backstage/frontend-plugin-api';
import { Content, Page } from '@backstage/core-components';
import HomeIcon from '@material-ui/icons/Home';
import { ArrowRight, Boxes, Cloud, Github, Network, Layers } from 'lucide-react';
import { Link } from 'react-router-dom';

const destinations = [
  {
    title: 'Service catalog',
    subtitle: 'Components and APIs',
    href: '/catalog',
    tone: 'catalog',
    icon: Boxes,
  },
  {
    title: 'Kubernetes',
    subtitle: 'Clusters and workloads',
    href: '/kdashm',
    tone: 'kubernetes',
    icon: Network,
  },
  {
    title: 'Azure cloud',
    subtitle: 'Subscriptions and resources',
    href: '/cloud',
    tone: 'cloud',
    icon: Cloud,
  },
  {
    title: 'GitHub',
    subtitle: 'Repositories and activity',
    href: '/github-org',
    tone: 'github',
    icon: Github,
  },
  {
    title: 'Infra Agent',
    subtitle: 'Terraform & cloud deployment',
    href: '/infra-agent',
    tone: 'cloud',
    icon: Layers,
  },
];

const PortalHome = () => (
  <Page themeId="home">
    <Content noPadding>
      <main className="apple-home">
        <section className="apple-hero" aria-labelledby="portal-title">
          <img
            className="apple-hero-image"
            src="https://images.unsplash.com/photo-1558494949-ef010cbdcc31?auto=format&fit=crop&w=2400&q=85"
            alt="Illuminated server racks in a data center"
          />
          <div className="apple-hero-shade" />
          <div className="apple-hero-copy">
            <p className="apple-eyebrow"><span /> BACKSTAGE SKYNET</p>
            <h1 id="portal-title">Your platform.<br />All in one place.</h1>
            <p className="apple-hero-caption">Services, clusters, and cloud resources.</p>
            <div className="apple-hero-actions">
              <Link className="apple-button apple-button-primary" to="/catalog">
                <Boxes size={17} aria-hidden="true" />
                Browse catalog
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
              <Link className="apple-button apple-button-glass" to="/kdashm">
                <Network size={17} aria-hidden="true" />
                Open Kubernetes
              </Link>
            </div>
          </div>
          <div className="apple-hero-index" aria-hidden="true">PLATFORM OVERVIEW <span>01</span></div>
        </section>

        <section className="apple-shelf" aria-labelledby="destinations-title">
          <div className="apple-shelf-heading">
            <div>
              <p className="apple-eyebrow">EXPLORE</p>
              <h2 id="destinations-title">Your workspace</h2>
            </div>
            <span className="apple-shelf-count">05 DESTINATIONS</span>
          </div>
          <div className="apple-card-row">
            {destinations.map(destination => {
              const Icon = destination.icon;
              return (
                <Link
                  className={`apple-destination apple-destination-${destination.tone}`}
                  key={destination.href}
                  to={destination.href}
                >
                  <span className="apple-card-icon"><Icon size={20} aria-hidden="true" /></span>
                  <span className="apple-card-copy">
                    <span className="apple-card-title">{destination.title}</span>
                    <span className="apple-card-subtitle">{destination.subtitle}</span>
                  </span>
                  <ArrowRight className="apple-card-arrow" size={18} aria-hidden="true" />
                </Link>
              );
            })}
          </div>
        </section>
      </main>
    </Content>
  </Page>
);

const portalHomePage = PageBlueprint.make({
  name: 'portal-home',
  params: {
    path: '/',
    title: 'Home',
    icon: <HomeIcon />,
    loader: async () => <PortalHome />,
  },
});

export const portalHomeModule = createFrontendModule({
  pluginId: 'app',
  extensions: [portalHomePage],
});
