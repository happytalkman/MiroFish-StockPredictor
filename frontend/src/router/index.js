import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import Dashboard from '../views/Dashboard.vue'
// 대시보드 외 화면은 필요할 때 불러와 초기 번들 크기를 줄인다.
const Process = () => import('../views/MainView.vue')
const SimulationView = () => import('../views/SimulationView.vue')
const SimulationRunView = () => import('../views/SimulationRunView.vue')
const ReportView = () => import('../views/ReportView.vue')
const InteractionView = () => import('../views/InteractionView.vue')
const StockPrediction = () => import('../views/StockPrediction.vue')
const Settings = () => import('../views/Settings.vue')
const Performance = () => import('../views/Performance.vue')
const Reports = () => import('../views/Reports.vue')
const Watchlist = () => import('../views/Watchlist.vue')
const Ontology = () => import('../views/Ontology.vue')

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: Dashboard
  },
  {
    path: '/lab',
    name: 'Home',
    component: Home
  },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true
  },
  {
    path: '/stock',
    name: 'StockPrediction',
    component: StockPrediction
  },
  {
    path: '/settings',
    name: 'Settings',
    component: Settings
  },
  { path: '/reports', name: 'Reports', component: Reports },
  { path: '/watchlist', name: 'Watchlist', component: Watchlist },
  { path: '/performance', name: 'Performance', component: Performance },
  { path: '/ontology', name: 'Ontology', component: Ontology }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
