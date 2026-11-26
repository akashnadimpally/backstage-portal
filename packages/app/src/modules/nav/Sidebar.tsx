import {
  Sidebar,
  SidebarDivider,
  SidebarGroup,
  SidebarItem,
  SidebarScrollWrapper,
  SidebarSpace,
} from '@backstage/core-components';
import { NavContentBlueprint } from '@backstage/plugin-app-react';
import { SidebarLogo } from './SidebarLogo';
import MenuIcon from '@material-ui/icons/Menu';
import SearchIcon from '@material-ui/icons/Search';
import GitHubIcon from '@material-ui/icons/GitHub';
import DashboardIcon from '@material-ui/icons/Dashboard';
import CloudIcon from '@material-ui/icons/Cloud';
import HomeIcon from '@material-ui/icons/Home';
import PeopleIcon from '@material-ui/icons/People';
import BuildIcon from '@material-ui/icons/Build';
import LayersIcon from '@material-ui/icons/Layers';
import { SidebarSearchModal } from '@backstage/plugin-search';
import { UserSettingsSignInAvatar } from '@backstage/plugin-user-settings';
import { NotificationsSidebarItem } from '@backstage/plugin-notifications';

export const SidebarContent = NavContentBlueprint.make({
  params: {
    component: ({ navItems }) => {
      const nav = navItems.withComponent(item => (
        <SidebarItem icon={() => item.icon} to={item.href} text={item.title} />
      ));

      // Skipped items
      nav.take('page:search'); // Using search modal instead
      nav.take('page:notifications'); // Using NotificationsSidebarItem manually instead
      nav.take('page:home'); // The portal home is registered by the app module

      return (
        <Sidebar>
          <SidebarLogo />
          <SidebarGroup label="Search" icon={<SearchIcon />} to="/search">
            <SidebarSearchModal />
          </SidebarGroup>
          <SidebarDivider />
          <SidebarGroup label="Menu" icon={<MenuIcon />}>
            <SidebarItem icon={HomeIcon} to="/" text="Home" />
            {nav.take('page:catalog')}
            {nav.take('page:scaffolder')}
            <SidebarItem icon={GitHubIcon} to="/github-org" text="GitHub Org" />
            <SidebarItem icon={DashboardIcon} to="/kdashm" text="Kubernetes Dashboard" />
            <SidebarItem icon={CloudIcon} to="/cloud" text="Cloud" />
            <SidebarItem icon={PeopleIcon} to="/user-admin" text="Local accounts" />
            <SidebarItem icon={BuildIcon} to="/pipelines" text="Pipelines" />
            <SidebarItem icon={LayersIcon} to="/infra-agent" text="Infra Agent" />
            <SidebarDivider />
            <SidebarScrollWrapper>
              {nav.rest({ sortBy: 'title' })}
            </SidebarScrollWrapper>
          </SidebarGroup>
          <SidebarSpace />
          <SidebarDivider />
          <NotificationsSidebarItem />
          <SidebarDivider />
          <SidebarGroup
            label="Settings"
            icon={<UserSettingsSignInAvatar />}
            to="/settings"
          >
            {nav.take('page:app-visualizer')}
            {nav.take('page:user-settings')}
          </SidebarGroup>
        </Sidebar>
      );
    },
  },
});
