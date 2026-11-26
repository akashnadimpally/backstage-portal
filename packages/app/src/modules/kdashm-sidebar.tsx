'use client';
import { useState } from 'react';
import { LayoutDashboard, Box, Layers, Shield, Settings, Server, Network, Database, Cpu, Activity, Zap, Clock, Key, Plus, X, TrendingUp, Terminal, FileCode, Eye, Trash2, RefreshCcw, User, ShipWheel, Store, GitBranch } from 'lucide-react';
import useSWR from 'swr';
import { motion, AnimatePresence } from 'framer-motion';
import { kdashmFetch } from './kdashm-api';

const navItems = [
  { id: 'all', label: 'Overview', icon: LayoutDashboard },
  { id: 'visualizer', label: 'Resource Map', icon: GitBranch },
  { id: 'namespaces', label: 'Namespaces', icon: Box },
  { id: 'pods', label: 'Pods', icon: Box },
  { id: 'deployments', label: 'Deployments', icon: Layers },
  { id: 'replicaSets', label: 'ReplicaSets', icon: Layers },
  { id: 'daemonSets', label: 'DaemonSets', icon: Layers },
  { id: 'statefulSets', label: 'StatefulSets', icon: Layers },
  { id: 'services', label: 'Services', icon: Network },
  { id: 'ingresses', label: 'Ingresses', icon: Network },
  { id: 'configMaps', label: 'ConfigMaps', icon: Settings },
  { id: 'secrets', label: 'Secrets', icon: Shield },
  { id: 'jobs', label: 'Jobs', icon: Zap },
  { id: 'cronJobs', label: 'CronJobs', icon: Clock },
  { id: 'hpas', label: 'HPA Autoscaling', icon: TrendingUp },
  { id: 'roles', label: 'Roles', icon: Key },
  { id: 'clusterRoles', label: 'Cluster Roles', icon: Key },
  { id: 'roleBindings', label: 'Role Bindings', icon: Key },
  { id: 'clusterRoleBindings', label: 'Cluster Role Bindings', icon: Key },
  { id: 'serviceAccounts', label: 'Service Accounts', icon: User },
  { id: 'pvcs', label: 'PVCs', icon: Database },
  { id: 'pvs', label: 'PVs', icon: Database },
  { id: 'storageClasses', label: 'Storage Classes', icon: Database },
  { id: 'serviceMesh', label: 'Service Mesh', icon: Network },
  { id: 'helmReleases', label: 'Helm Releases', icon: ShipWheel },
  { id: 'helmCharts', label: 'App Catalog', icon: Store },
  { id: 'nodes', label: 'Nodes', icon: Server },
  { id: 'analytics', label: 'Analytics', icon: TrendingUp },
  { id: 'metrics', label: 'Metrics', icon: Activity },
  { id: 'crds', label: 'CRDs', icon: Cpu },
  { id: 'networkPolicies', label: 'Net Policies', icon: Activity },
];

const fetcher = (url: string) => kdashmFetch(url).then(res => res.json());

export default function Sidebar({ active, setActive }: { active: string, setActive: (id: string) => void }) {
  const [showAddCluster, setShowAddCluster] = useState(false);
  const [kubeconfig, setKubeconfig] = useState('');
  const { data: contextData, mutate: mutateContext } = useSWR('/api/contexts', fetcher);
  const { data: aksData, mutate: mutateAks } = useSWR('/api/contexts/aks', fetcher, {
    refreshInterval: 60000,
  });

  const aksClusterValue = (cluster: any) =>
    `aks:${cluster.subscriptionId}:${cluster.resourceGroup}:${cluster.name}`;

  const switchContext = async (name: string) => {
    await kdashmFetch('/api/contexts', {
      method: 'POST',
      body: JSON.stringify({ context: name }),
      headers: { 'Content-Type': 'application/json' }
    });
    mutateContext();
    window.location.reload();
  };

  const connectAksCluster = async (cluster: any) => {
    const response = await kdashmFetch('/api/contexts/aks/connect', {
      method: 'POST',
      body: JSON.stringify(cluster),
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) {
      const result = await response.json();
      window.alert(result.error || 'Could not connect to AKS cluster');
      return;
    }
    await mutateContext();
    await mutateAks();
    window.location.reload();
  };

  const handleClusterChange = (value: string) => {
    const aksCluster = aksData?.clusters?.find((cluster: any) => aksClusterValue(cluster) === value);
    if (aksCluster) connectAksCluster(aksCluster);
    else switchContext(value);
  };

  const handleAddCloudCluster = async () => {
    await kdashmFetch('/api/contexts', {
      method: 'POST',
      body: JSON.stringify({ kubeconfig }),
      headers: { 'Content-Type': 'application/json' }
    });
    setKubeconfig('');
    setShowAddCluster(false);
    mutateContext();
    window.location.reload();
  };

  return (
    <aside className="sidebar glass" style={{ overflowY: 'auto' }}>
      <div className="logo-section">
        <h1 className="gradient-text" style={{ fontSize: '1.5rem', fontWeight: 'bold' }}>kDashM</h1>
        <p style={{ fontSize: '0.75rem', opacity: 0.6 }}>Cloud Native IDE</p>
      </div>

      <div style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <p style={{ fontSize: '0.7rem', opacity: 0.5, textTransform: 'uppercase' }}>Active Cluster</p>
          <button onClick={() => setShowAddCluster(true)} style={{ background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer' }}>
            <Plus size={14} />
          </button>
        </div>
        <select
          value={contextData?.currentContext || ''}
          onChange={(e) => handleClusterChange(e.target.value)}
          className="glass"
          style={{ width: '100%', padding: '0.5rem', borderRadius: '8px', border: 'none', color: '#fff', fontSize: '0.85rem', cursor: 'pointer' }}
        >
          <optgroup label="Kubeconfig contexts">
            {contextData?.contexts?.map((ctx: any) => (
              <option key={ctx.name} value={ctx.name} style={{ background: '#1e1e23' }}>
                {ctx.name.length > 20 ? ctx.name.substring(0, 17) + '...' : ctx.name}
              </option>
            ))}
          </optgroup>
          {aksData?.clusters?.length > 0 && (
            <optgroup label="Azure AKS (select to connect)">
              {aksData.clusters.map((cluster: any) => (
                <option key={aksClusterValue(cluster)} value={aksClusterValue(cluster)} style={{ background: '#1e1e23' }}>
                  {cluster.name} ({cluster.subscriptionName})
                </option>
              ))}
            </optgroup>
          )}
        </select>
        {aksData?.clusters?.length === 0 && (
          <p style={{ fontSize: '0.7rem', opacity: 0.45 }}>No AKS clusters found in signed-in Azure subscriptions</p>
        )}
      </div>

      <nav style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setActive(item.id)}
            className={`nav-item ${active === item.id ? 'active' : ''}`}
            style={{ border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left', width: '100%' }}
          >
            <item.icon size={18} />
            <span style={{ fontSize: '0.9rem' }}>{item.label}</span>
          </button>
        ))}
      </nav>

      <AnimatePresence>
        {showAddCluster && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.8)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}
          >
            <motion.div
              initial={{ scale: 0.9 }} animate={{ scale: 1 }} exit={{ scale: 0.9 }}
              className="glass" style={{ width: '400px', padding: '1.5rem', borderRadius: '20px' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
                <h3 style={{ fontWeight: '800' }}>Add Cloud Cluster</h3>
                <button onClick={() => setShowAddCluster(false)} style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer' }}><X size={20} /></button>
              </div>
              <p style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '1rem' }}>Paste your Kubeconfig snippet (AKS/EKS/GKE) to connect.</p>
              <textarea
                value={kubeconfig}
                onChange={(e) => setKubeconfig(e.target.value)}
                placeholder="apiVersion: v1..."
                style={{ width: '100%', height: '150px', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--glass-border)', borderRadius: '12px', color: '#fff', padding: '0.75rem', fontSize: '0.8rem', fontFamily: 'monospace', marginBottom: '1rem' }}
              />
              <button
                onClick={handleAddCloudCluster}
                className="glass"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '12px', border: 'none', background: 'var(--primary)', color: '#fff', fontWeight: 'bold', cursor: 'pointer' }}
              >
                Connect Cluster
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </aside>
  );
}
