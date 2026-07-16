import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  ActivityIndicator,
  Alert,
  Linking,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

// ─── Tipos ────────────────────────────────────────────────────────────────────

type Screen = "login" | "config" | "dashboard" | "alerts" | "opportunities" | "grid";

type ProviderStatus = {
  provider: string;
  configured: boolean;
  label?: string;
  sandbox?: boolean;
  maskedApiKey?: string;
  maskedSecret?: string;
  hasPassphrase?: boolean;
};

const MASKED_PLACEHOLDER = "••••••••";

type CredentialSummary = {
  id: string;
  providerKind: "exchange" | "ai" | "scraper";
  provider: string;
  label: string;
  sandbox: boolean;
  updatedAt: string;
};

type BalanceResponse = {
  exchange: string;
  sandbox: boolean;
  fetchedAt: string;
  note?: string;
  balances: Array<{
    asset: string;
    free: number;
    used: number;
    total: number;
  }>;
};

type EngineStatus = {
  enabled: boolean;
  exchange: string;
  symbol: string;
  sandbox: boolean;
  startedAt: string | null;
  lastCheckedAt: string | null;
  lastTicker: {
    bid: number | null;
    ask: number | null;
    last: number | null;
  } | null;
  lastError: string | null;
};

type FlippingEngineStatus = {
  motor: "tech" | "real-estate" | "saas" | "grid";
  enabled: boolean;
  startedAt: string | null;
  lastRunAt: string | null;
  lastResult: Record<string, unknown> | null;
  lastError: string | null;
};

type AlertRecord = {
  id: string;
  motor: "tech" | "crypto" | "real-estate" | "saas";
  title: string;
  description: string;
  sourceUrl: string | null;
  severity: "low" | "medium" | "high";
  read: boolean;
  createdAt: string;
};

type OpportunityRecord = {
  id: string;
  motor: "real-estate" | "saas";
  title: string;
  description: string;
  sourceUrl: string | null;
  aiAnalysis: string;
  estimatedValue: string | null;
  estimatedRepair: string | null;
  dealScore: number | null;
  tags: string[];
  status: "new" | "reviewed" | "archived";
  createdAt: string;
};

// ─── Paleta ────────────────────────────────────────────────────────────────────

const colors = {
  bg: "#08111f",
  panel: "#101c2c",
  panelAlt: "#13243a",
  border: "#1e3552",
  text: "#ebf2ff",
  muted: "#9cb3d1",
  accent: "#4f8cff",
  success: "#20c997",
  warning: "#f4b740",
  danger: "#ff6b6b",
  purple: "#a78bfa",
};

const defaultEngineStatus: EngineStatus = {
  enabled: false,
  exchange: "okx",
  symbol: "BTC/USDT",
  sandbox: true,
  startedAt: null,
  lastCheckedAt: null,
  lastTicker: null,
  lastError: null,
};

const defaultFlippingEngines: FlippingEngineStatus[] = [
  { motor: "grid", enabled: true, startedAt: null, lastRunAt: null, lastResult: null, lastError: null },
  { motor: "tech", enabled: false, startedAt: null, lastRunAt: null, lastResult: null, lastError: null },
  { motor: "real-estate", enabled: false, startedAt: null, lastRunAt: null, lastResult: null, lastError: null },
  { motor: "saas", enabled: false, startedAt: null, lastRunAt: null, lastResult: null, lastError: null },
];

// ─── Componente principal ─────────────────────────────────────────────────────

export default function App() {
  const [screen, setScreen] = useState<Screen>("login");
  const [apiBaseUrl, setApiBaseUrl] = useState("http://[2800:484:a480:9b50:bc18:4d55:828:5436]:4000");
  const [email, setEmail] = useState("operator@jk.local");
  const [password, setPassword] = useState("123456");
  const [userLabel, setUserLabel] = useState("JK Operator");
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [displayName, setDisplayName] = useState("JK Operator");

  // Sprint 1 & 2
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [balances, setBalances] = useState<BalanceResponse | null>(null);
  const [engineStatus, setEngineStatus] = useState<EngineStatus>(defaultEngineStatus);
  const [activeExchange, setActiveExchange] = useState("okx");
  const [activeSandbox, setActiveSandbox] = useState(true);
  const [exchangeConfigured, setExchangeConfigured] = useState(false);
  const [exchangeApiKey, setExchangeApiKey] = useState("");
  const [exchangeSecret, setExchangeSecret] = useState("");
  const [exchangePassphrase, setExchangePassphrase] = useState("");
  const [openRouterApiKey, setOpenRouterApiKey] = useState("");
  const [firecrawlApiKey, setFirecrawlApiKey] = useState("");
  const [providerStatuses, setProviderStatuses] = useState<ProviderStatus[]>([]);

  useEffect(() => {
    const cred = credentials.find(c => c.provider === activeExchange && c.sandbox === activeSandbox);
    setExchangeConfigured(!!cred);
    if (cred) {
      setExchangeApiKey(MASKED_PLACEHOLDER);
      setExchangeSecret(MASKED_PLACEHOLDER);
      if (activeExchange !== "binance") setExchangePassphrase(MASKED_PLACEHOLDER);
    } else {
      setExchangeApiKey("");
      setExchangeSecret("");
      setExchangePassphrase("");
    }
  }, [activeExchange, activeSandbox, credentials]);

  // Sprint 3 & 4
  const [flippingEngines, setFlippingEngines] = useState<FlippingEngineStatus[]>(defaultFlippingEngines);
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [opportunities, setOpportunities] = useState<OpportunityRecord[]>([]);
  const [selectedOpportunity, setSelectedOpportunity] = useState<OpportunityRecord | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);

  const [gridWorkerStatus, setGridWorkerStatus] = useState("Offline");
  const [gridLogs, setGridLogs] = useState<string[]>([]);
  const [gridMetrics, setGridMetrics] = useState<any>(null);
  const [backtestResult, setBacktestResult] = useState<any>(null);
  const [backtestTop10, setBacktestTop10] = useState<any[]>([]);
  const [selectedBacktestSymbol, setSelectedBacktestSymbol] = useState<string | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [showGridConfig, setShowGridConfig] = useState(false);
  const [uaoGridStatus, setUaoGridStatus] = useState<any>(null);
  
  const [gridBaseCapital, setGridBaseCapital] = useState("50");
  const [gridMaxLeverage, setGridMaxLeverage] = useState("15");

  const loadGridConfig = async () => {
    try {
      const cap = await AsyncStorage.getItem("@grid_base_capital");
      const lev = await AsyncStorage.getItem("@grid_max_leverage");
      if (cap) setGridBaseCapital(cap);
      if (lev) setGridMaxLeverage(lev);
    } catch (e) {
      // silent
    }
  };

  const saveGridConfig = async () => {
    try {
      await AsyncStorage.setItem("@grid_base_capital", gridBaseCapital);
      await AsyncStorage.setItem("@grid_max_leverage", gridMaxLeverage);
      await callApi("/api/grid/config", {
        method: "POST",
        body: JSON.stringify({ gridBaseCapital, gridMaxLeverage }),
      });
      Alert.alert("Éxito", "Configuración de Grid guardada localmente y en el motor.");
    } catch (e) {
      Alert.alert("Error", "No se pudo guardar la configuración.");
    }
  };

  const totalCapital = useMemo(() => {
    return balances?.balances.reduce((sum, entry) => sum + entry.total, 0) ?? 0;
  }, [balances]);

  useEffect(() => {
    if (screen === "dashboard") {
      void Promise.all([loadEngineConfig(), loadCredentials(), loadBalances(), loadEngineStatus(), loadFlippingEngines()]);
    }
    if (screen === "alerts") {
      void loadAlerts();
    }
    if (screen === "opportunities") {
      void loadOpportunities();
    }
    if (screen === "config") {
      void loadEngineConfig();
      void loadCredentialStatus();
      void loadCredentials();
    }
    
    let gridInterval: ReturnType<typeof setInterval>;
    if (screen === "grid") {
      void loadGridConfig();
      void loadGridMetrics();
      void loadUaoGridStatus();
      gridInterval = setInterval(() => {
        void loadGridMetrics();
        void loadUaoGridStatus();
      }, 2000);
    }
    
    return () => {
      if (gridInterval) clearInterval(gridInterval);
    };
  }, [screen]);

  // ─── API helper ────────────────────────────────────────────────────────────

  const callApi = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const response = await fetch(`${apiBaseUrl}${path}`, {
      headers: {
        ...headers,
        ...(init?.headers ?? {}),
      },
      ...init,
    });

    if (!response.ok) {
      const data = (await response.json().catch(() => null)) as { error?: string } | null;
      throw new Error(data?.error ?? "No fue posible completar la solicitud.");
    }

    return (await response.json()) as T;
  };

  // ─── Handlers Sprint 1 & 2 ────────────────────────────────────────────────

  const handleLogin = async (offline = false) => {
    setLoading(true);
    try {
      if (!offline) {
        if (isRegistering) {
          const result = await callApi<{ token: string; user: { displayName: string } }>("/api/auth/register", {
            method: "POST",
            body: JSON.stringify({ email, password, displayName }),
          });
          setToken(result.token);
          setUserLabel(result.user.displayName);
        } else {
          const result = await callApi<{ token: string; user: { displayName: string } }>("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({ email, password }),
          });
          setToken(result.token);
          setUserLabel(result.user.displayName);
        }
      }
      setScreen("dashboard");
    } catch (error) {
      Alert.alert("Login", error instanceof Error ? error.message : "No se pudo iniciar sesión.");
    } finally {
      setLoading(false);
    }
  };

  const loadCredentials = async () => {
    try {
      const result = await callApi<{ credentials: CredentialSummary[] }>("/api/keys");
      setCredentials(result.credentials);
    } catch (error) {
      Alert.alert("Credenciales", error instanceof Error ? error.message : "No se pudo cargar la bóveda.");
    }
  };

  const loadEngineConfig = async () => {
    try {
      const config = await callApi<{ activeExchange: string; useSandbox: boolean }>("/api/engine/config");
      setActiveExchange(config.activeExchange);
      setActiveSandbox(config.useSandbox);
    } catch {
      // silencioso
    }
  };

  const loadCredentialStatus = async () => {
    try {
      const result = await callApi<{ providers: ProviderStatus[] }>("/api/keys/status");
      setProviderStatuses(result.providers);
      
      for (const p of result.providers) {
        if (!p.configured) continue;
        if (p.provider === "openrouter") {
          if (!openRouterApiKey || openRouterApiKey === MASKED_PLACEHOLDER) setOpenRouterApiKey(p.maskedApiKey ?? MASKED_PLACEHOLDER);
        } else if (p.provider === "firecrawl") {
          if (!firecrawlApiKey || firecrawlApiKey === MASKED_PLACEHOLDER) setFirecrawlApiKey(p.maskedApiKey ?? MASKED_PLACEHOLDER);
        }
      }
    } catch {
      // silencioso — primer uso sin credenciales
    }
  };

  const loadBalances = async () => {
    try {
      const result = await callApi<BalanceResponse>("/api/balances");
      setBalances(result);
    } catch (error) {
      Alert.alert("Balances", error instanceof Error ? error.message : "No se pudo consultar balances.");
    }
  };

  const loadEngineStatus = async () => {
    try {
      const result = await callApi<EngineStatus>("/api/engine/status");
      setEngineStatus(result);
    } catch (error) {
      Alert.alert("Motor cripto", error instanceof Error ? error.message : "No se pudo consultar el estado.");
    }
  };

  const saveCredential = async (body: unknown, successMessage: string) => {
    setLoading(true);
    try {
      await callApi("/api/keys", { method: "POST", body: JSON.stringify(body) });
      Alert.alert("Bóveda", successMessage);
      await loadCredentials();
    } catch (error) {
      Alert.alert("Bóveda", error instanceof Error ? error.message : "No se pudo guardar la credencial.");
    } finally {
      setLoading(false);
    }
  };

  const isMasked = (value: string) => value.includes("••••");

  const saveExchangeKeys = async () => {
    if (isMasked(exchangeApiKey) && isMasked(exchangeSecret)) {
      Alert.alert("Bóveda", `Las credenciales de ${activeExchange} ya están configuradas. Ingresa valores nuevos solo si deseas cambiarlas.`);
      return;
    }
    await saveCredential(
      { providerKind: "exchange", provider: activeExchange, label: `${activeExchange} (${activeSandbox ? "Sandbox" : "Live"})`, sandbox: activeSandbox,
        payload: { apiKey: exchangeApiKey, secret: exchangeSecret, passphrase: exchangePassphrase } },
      `Credenciales de ${activeExchange} guardadas.`,
    );
  };

  const saveOpenRouterKey = async () => {
    if (isMasked(openRouterApiKey)) {
      Alert.alert("Bóveda", "La API key de OpenRouter ya está configurada. Ingresa un valor nuevo solo si deseas cambiarla.");
      return;
    }
    await saveCredential(
      { providerKind: "ai", provider: "openrouter", label: "OpenRouter", payload: { apiKey: openRouterApiKey } },
      "API key de OpenRouter guardada.",
    );
  };

  const saveFirecrawlKey = async () => {
    if (isMasked(firecrawlApiKey)) {
      Alert.alert("Bóveda", "La API key de Firecrawl ya está configurada. Ingresa un valor nuevo solo si deseas cambiarla.");
      return;
    }
    await saveCredential(
      { providerKind: "scraper", provider: "firecrawl", label: "Firecrawl", payload: { apiKey: firecrawlApiKey } },
      "API key de Firecrawl guardada.",
    );
  };

  const saveActiveEngineConfig = async (exchange: string, sandbox: boolean) => {
    setLoading(true);
    try {
      await callApi("/api/engine/config", {
        method: "POST",
        body: JSON.stringify({ activeExchange: exchange, useSandbox: sandbox }),
      });
      setActiveExchange(exchange);
      setActiveSandbox(sandbox);
      Alert.alert("Éxito", "Configuración activa del motor actualizada");
      await loadBalances();
    } catch (error) {
      Alert.alert("Error", error instanceof Error ? error.message : "No se pudo actualizar la configuración.");
    } finally {
      setLoading(false);
    }
  };

  const toggleCryptoEngine = async (enabled: boolean) => {
    setLoading(true);
    try {
      const result = await callApi<EngineStatus>("/api/engine/toggle", {
        method: "POST",
        body: JSON.stringify({ enabled, exchange: activeExchange, symbol: "BTC/USDT", sandbox: activeSandbox }),
      });
      setEngineStatus(result);
    } catch (error) {
      Alert.alert("Motor cripto", error instanceof Error ? error.message : "No se pudo cambiar el estado.");
    } finally {
      setLoading(false);
    }
  };

  // ─── Handlers Sprint 3 & 4 ────────────────────────────────────────────────

  const loadFlippingEngines = async () => {
    try {
      const result = await callApi<{ engines: FlippingEngineStatus[] }>("/api/engines");
      setFlippingEngines(result.engines);
    } catch {
      // silencioso — puede no existir aún en la DB
    }
  };

  const toggleFlippingEngine = async (motor: FlippingEngineStatus["motor"], enabled: boolean) => {
    setLoading(true);
    try {
      const result = await callApi<FlippingEngineStatus>("/api/engines/toggle", {
        method: "POST",
        body: JSON.stringify({ motor, enabled }),
      });
      setFlippingEngines((prev) => prev.map((e) => (e.motor === motor ? result : e)));
    } catch (error) {
      Alert.alert("Motor", error instanceof Error ? error.message : "No se pudo cambiar el estado.");
    } finally {
      setLoading(false);
    }
  };

  const loadAlerts = async () => {
    try {
      const result = await callApi<{ alerts: AlertRecord[] }>("/api/alerts");
      setAlerts(result.alerts);
      setUnreadCount(result.alerts.filter((a) => !a.read).length);
    } catch (error) {
      Alert.alert("Alertas", error instanceof Error ? error.message : "No se pudieron cargar las alertas.");
    }
  };

  const markAlertAsRead = async (id: string) => {
    try {
      await callApi(`/api/alerts/${id}/read`, { method: "POST" });
      setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, read: true } : a)));
      setUnreadCount((n) => Math.max(0, n - 1));
    } catch {
      // silencioso
    }
  };

  const loadOpportunities = async () => {
    try {
      const result = await callApi<{ opportunities: OpportunityRecord[] }>("/api/opportunities");
      setOpportunities(result.opportunities);
    } catch (error) {
      Alert.alert("Oportunidades", error instanceof Error ? error.message : "No se pudieron cargar las oportunidades.");
    }
  };

  const updateOpportunityStatus = async (id: string, status: "reviewed" | "archived") => {
    try {
      await callApi(`/api/opportunities/${id}/status`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      setOpportunities((prev) => prev.map((o) => (o.id === id ? { ...o, status } : o)));
      if (selectedOpportunity?.id === id) {
        setSelectedOpportunity((prev) => (prev ? { ...prev, status } : null));
      }
    } catch (error) {
      Alert.alert("Error", error instanceof Error ? error.message : "No se pudo actualizar.");
    }
  };

  // ─── Helpers de render ───────────────────────────────────────────────────

  const loadGridMetrics = async () => {
    try {
      const res = await callApi<any>("/api/grid/metrics");
      if (res && res.status) {
        setGridMetrics(res);
        setGridWorkerStatus(res.status);
        if (res.best_opportunity) {
          setBacktestResult(res.best_opportunity);
        }
        if (res.backtest_top10 && Array.isArray(res.backtest_top10)) {
          setBacktestTop10(res.backtest_top10);
        }
        if (res.logs && Array.isArray(res.logs)) {
          setGridLogs(res.logs);
        }
      }
    } catch (e: any) {
      // silent
    }
  };

  const loadUaoGridStatus = async () => {
    try {
      const res = await callApi<any>("/api/grid/status");
      setUaoGridStatus(res);
    } catch (e: any) {
      // silent
    }
  };

  const getFlippingEngine = (motor: FlippingEngineStatus["motor"]) =>
    flippingEngines.find((e) => e.motor === motor);

  const renderTopBar = () => (
    <View style={styles.topBar}>
      <Text style={styles.brand}>JK-Flipping</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={styles.tabRow}>
          <TabButton active={screen === "dashboard"} label="Dashboard" onPress={() => setScreen("dashboard")} />
          <TabButton
            active={screen === "alerts"}
            label={unreadCount > 0 ? `Alertas (${unreadCount})` : "Alertas"}
            onPress={() => setScreen("alerts")}
          />
          <TabButton active={screen === "opportunities"} label="Oportunidades" onPress={() => setScreen("opportunities")} />
          <TabButton active={screen === "grid"} label="Grid Worker" onPress={() => setScreen("grid")} />
          <TabButton active={screen === "config"} label="Bóveda" onPress={() => setScreen("config")} />
        </View>
      </ScrollView>
    </View>
  );

  const severityColor = (severity: AlertRecord["severity"]) => {
    if (severity === "high") return colors.danger;
    if (severity === "medium") return colors.warning;
    return colors.muted;
  };

  const dealScoreColor = (score: number | null) => {
    if (!score) return colors.muted;
    if (score >= 8) return colors.success;
    if (score >= 5) return colors.warning;
    return colors.danger;
  };

  const motorLabel: Record<string, string> = {
    tech: "💻 Retail Tech",
    "real-estate": "🏠 Inmobiliario",
    saas: "🚀 Micro-SaaS",
    crypto: "🪙 Cripto",
    grid: "🤖 Grid Cuantitativo",
  };

  // ─── JSX ─────────────────────────────────────────────────────────────────

  return (
    <View style={styles.safeArea}>
      <StatusBar style="light" />

      {/* ── LOGIN ── */}
      {screen === "login" ? (
        <ScrollView contentContainerStyle={styles.loginContainer}>
          <Text style={styles.eyebrow}>Centro de mando</Text>
          <Text style={styles.title}>Automatiza los motores de JK-Flipping</Text>
          <Text style={styles.subtitle}>
            Bóveda segura + 4 motores de flipping con IA, scraping y arbitraje cripto.
          </Text>

          <Panel>
            <Text style={styles.sectionTitle}>Conexión API</Text>
            <Field label="Base URL del backend" value={apiBaseUrl} onChangeText={setApiBaseUrl} autoCapitalize="none" />
            <Text style={styles.helper}>
              En emulador Android usa `http://10.0.2.2:4000`. En dispositivo físico cambia a tu IP local.
            </Text>
          </Panel>

          <Panel>
            <Text style={styles.sectionTitle}>{isRegistering ? "Crear Cuenta" : "Login"}</Text>
            {isRegistering && (
              <Field label="Nombre (Display Name)" value={displayName} onChangeText={setDisplayName} />
            )}
            <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" />
            <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry />
            <Pressable style={styles.primaryButton} onPress={() => void handleLogin(false)}>
              <Text style={styles.primaryButtonText}>{isRegistering ? "Registrarse" : "Entrar con backend"}</Text>
            </Pressable>
            <Pressable style={styles.secondaryButton} onPress={() => setIsRegistering(!isRegistering)}>
              <Text style={styles.secondaryButtonText}>{isRegistering ? "¿Ya tienes cuenta? Inicia sesión" : "¿No tienes cuenta? Regístrate"}</Text>
            </Pressable>
            <Pressable style={[styles.secondaryButton, { marginTop: 8 }]} onPress={() => void handleLogin(true)}>
              <Text style={styles.secondaryButtonText}>Entrar en demo local</Text>
            </Pressable>
          </Panel>
        </ScrollView>

      ) : (
        <ScrollView contentContainerStyle={styles.appContainer}>
          {renderTopBar()}
          <Text style={styles.welcome}>Operador activo: {userLabel}</Text>

          {/* ── BÓVEDA ── */}
          {screen === "config" && (
            <>
              <Panel>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                  <Text style={styles.sectionTitle}>Bóveda Exchange Activo</Text>
                  {exchangeConfigured && (
                    <View style={{ flexDirection: "row", alignItems: "center", backgroundColor: "rgba(32,201,151,0.15)", paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }}>
                      <Text style={{ color: colors.success, fontSize: 12, fontWeight: "700" }}>✅ Configurado</Text>
                    </View>
                  )}
                </View>
                
                <Text style={styles.fieldLabel}>Exchange</Text>
                <View style={{ flexDirection: "row", gap: 8, marginBottom: 8 }}>
                  {["okx", "binance", "bybit"].map(ex => (
                    <Pressable
                      key={ex}
                      style={[styles.tabButton, activeExchange === ex && styles.tabButtonActive, { flex: 1, alignItems: "center" }]}
                      onPress={() => setActiveExchange(ex)}
                    >
                      <Text style={[styles.tabLabel, activeExchange === ex && styles.tabLabelActive, { textTransform: "capitalize" }]}>{ex}</Text>
                    </Pressable>
                  ))}
                </View>

                <RowBetween
                  left="Modo sandbox"
                  right={
                    <Switch value={activeSandbox} onValueChange={(val) => setActiveSandbox(val)}
                      trackColor={{ false: "#38506f", true: "#2f6ae6" }} thumbColor="#ffffff" />
                  }
                />
                
                <Pressable style={styles.secondaryButtonCompact} onPress={() => saveActiveEngineConfig(activeExchange, activeSandbox)}>
                  <Text style={styles.secondaryButtonText}>Aplicar como Motor Activo</Text>
                </Pressable>

                <View style={{ height: 1, backgroundColor: colors.border, marginVertical: 12 }} />

                <Text style={styles.fieldLabel}>Credenciales para {activeExchange} ({activeSandbox ? 'Sandbox' : 'Live'})</Text>

                <Field label="API Key" value={exchangeApiKey} onChangeText={setExchangeApiKey} autoCapitalize="none" />
                <Field label="Secret" value={exchangeSecret} onChangeText={setExchangeSecret} secureTextEntry />
                {activeExchange !== "binance" && (
                  <Field label="Passphrase" value={exchangePassphrase} onChangeText={setExchangePassphrase} secureTextEntry />
                )}
                <Pressable style={styles.primaryButton} onPress={() => void saveExchangeKeys()}>
                  <Text style={styles.primaryButtonText}>Guardar Credenciales</Text>
                </Pressable>
              </Panel>

              <Panel>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <Text style={styles.sectionTitle}>OpenRouter (IA)</Text>
                  {providerStatuses.find(p => p.provider === "openrouter")?.configured && (
                    <View style={{ flexDirection: "row", alignItems: "center", backgroundColor: "rgba(32,201,151,0.15)", paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }}>
                      <Text style={{ color: colors.success, fontSize: 12, fontWeight: "700" }}>✅ Configurado</Text>
                    </View>
                  )}
                </View>
                <Text style={styles.helper}>Necesario para los motores Tech, Inmobiliario y SaaS.</Text>
                <Field label="API Key" value={openRouterApiKey} onChangeText={setOpenRouterApiKey} autoCapitalize="none" />
                <Pressable style={styles.primaryButton} onPress={() => void saveOpenRouterKey()}>
                  <Text style={styles.primaryButtonText}>Guardar OpenRouter</Text>
                </Pressable>
              </Panel>

              <Panel>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <Text style={styles.sectionTitle}>Firecrawl (Scraping)</Text>
                  {providerStatuses.find(p => p.provider === "firecrawl")?.configured && (
                    <View style={{ flexDirection: "row", alignItems: "center", backgroundColor: "rgba(32,201,151,0.15)", paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 }}>
                      <Text style={{ color: colors.success, fontSize: 12, fontWeight: "700" }}>✅ Configurado</Text>
                    </View>
                  )}
                </View>
                <Text style={styles.helper}>Necesario para los motores Tech, Inmobiliario y SaaS.</Text>
                <Field label="API Key" value={firecrawlApiKey} onChangeText={setFirecrawlApiKey} autoCapitalize="none" />
                <Pressable style={styles.primaryButton} onPress={() => void saveFirecrawlKey()}>
                  <Text style={styles.primaryButtonText}>Guardar Firecrawl</Text>
                </Pressable>
              </Panel>

              <Panel>
                <Text style={styles.sectionTitle}>Credenciales registradas</Text>
                {credentials.length === 0 ? (
                  <Text style={styles.helper}>Todavía no hay credenciales guardadas.</Text>
                ) : (
                  credentials.map((credential) => (
                    <View key={credential.id} style={styles.credentialRow}>
                      <Text style={styles.cardTitle}>{credential.label}</Text>
                      <Text style={styles.cardMeta}>
                        {credential.provider} · {credential.providerKind} · {credential.sandbox ? "sandbox" : "live"}
                      </Text>
                    </View>
                  ))
                )}
              </Panel>
            </>
          )}

          {/* ── ALERTAS (Sprint 3) ── */}
          {screen === "alerts" && (
            <>
              <Panel>
                <View style={styles.rowBetween}>
                  <Text style={styles.sectionTitle}>🔔 Alertas</Text>
                  <Pressable style={styles.secondaryButtonCompact} onPress={() => void loadAlerts()}>
                    <Text style={styles.secondaryButtonText}>Refrescar</Text>
                  </Pressable>
                </View>
                <Text style={styles.helper}>{unreadCount} sin leer · {alerts.length} total</Text>
              </Panel>

              {alerts.length === 0 ? (
                <Panel>
                  <Text style={styles.helper}>
                    Aún no hay alertas. Enciende el Motor Retail Tech desde el Dashboard para comenzar el escaneo.
                  </Text>
                </Panel>
              ) : (
                alerts.map((alert) => (
                  <Pressable key={alert.id} onPress={() => void markAlertAsRead(alert.id)}>
                    <View style={[styles.panel, !alert.read && { borderColor: severityColor(alert.severity) }]}>
                      <View style={styles.rowBetween}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.cardTitle}>{alert.title}</Text>
                          <Text style={[styles.badge, { color: severityColor(alert.severity) }]}>
                            {motorLabel[alert.motor] ?? alert.motor} · {alert.severity.toUpperCase()}
                          </Text>
                        </View>
                        {!alert.read && <View style={styles.unreadDot} />}
                      </View>
                      <Text style={styles.cardMeta}>{alert.description}</Text>
                      {alert.sourceUrl ? (
                        <Pressable onPress={() => Linking.openURL(alert.sourceUrl as string).catch(() => {})}>
                          <Text style={[styles.helper, { color: colors.accent, textDecorationLine: 'underline' }]} numberOfLines={1}>
                            {alert.sourceUrl}
                          </Text>
                        </Pressable>
                      ) : null}
                      <Text style={styles.helper}>{new Date(alert.createdAt).toLocaleString()}</Text>
                    </View>
                  </Pressable>
                ))
              )}
            </>
          )}

          {/* ── OPORTUNIDADES (Sprint 4) ── */}
          {screen === "opportunities" && (
            <>
              <Panel>
                <View style={styles.rowBetween}>
                  <Text style={styles.sectionTitle}>🎯 Oportunidades</Text>
                  <Pressable style={styles.secondaryButtonCompact} onPress={() => void loadOpportunities()}>
                    <Text style={styles.secondaryButtonText}>Refrescar</Text>
                  </Pressable>
                </View>
                <Text style={styles.helper}>Reportes generados por IA (Inmobiliario + SaaS)</Text>
              </Panel>

              {opportunities.length === 0 ? (
                <Panel>
                  <Text style={styles.helper}>
                    Aún no hay oportunidades detectadas. Enciende los motores Inmobiliario y SaaS desde el Dashboard.
                  </Text>
                </Panel>
              ) : (
                opportunities.map((opp) => (
                  <Pressable key={opp.id} onPress={() => setSelectedOpportunity(opp)}>
                    <View style={[styles.panel, { borderColor: dealScoreColor(opp.dealScore) }]}>
                      <View style={styles.rowBetween}>
                        <Text style={[styles.badge, { color: colors.muted }]}>
                          {opp.motor === "real-estate" ? "🏠" : "💻"} {opp.motor === "real-estate" ? "Inmobiliario" : "SaaS"}
                        </Text>
                        <View style={[styles.scoreChip, { backgroundColor: dealScoreColor(opp.dealScore) + "22" }]}>
                          <Text style={[styles.scoreText, { color: dealScoreColor(opp.dealScore) }]}>
                            ⭐ {opp.dealScore ?? "–"}/10
                          </Text>
                        </View>
                      </View>
                      <Text style={styles.cardTitle}>{opp.title}</Text>
                      <Text style={styles.cardMeta} numberOfLines={2}>{opp.description}</Text>
                      <View style={styles.tagRow}>
                        {(opp.tags ?? []).slice(0, 4).map((tag) => (
                          <View key={tag} style={styles.tag}>
                            <Text style={styles.tagText}>{tag}</Text>
                          </View>
                        ))}
                      </View>
                      <View style={styles.rowBetween}>
                        {opp.estimatedValue ? (
                          <Text style={[styles.cardMeta, { color: colors.success }]}>
                            💰 {opp.estimatedValue}
                          </Text>
                        ) : null}
                        <Text style={[styles.badge, { color: opp.status === "new" ? colors.warning : colors.muted }]}>
                          {opp.status.toUpperCase()}
                        </Text>
                      </View>
                    </View>
                  </Pressable>
                ))
              )}
            </>
          )}

          {/* 🔴 GRID WORKER 🔴 */}
          {screen === "grid" && (
            <>
              <Panel>
                <View style={styles.rowBetween}>
                  <Text style={styles.sectionTitle}>Estado del Worker</Text>
                  <Pressable onPress={() => setShowGridConfig(true)}>
                    <Text style={{ fontSize: 24 }}>⚙️</Text>
                  </Pressable>
                </View>
                
                <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 16, marginTop: 10 }}>
                  <View style={{
                    width: 12, height: 12, borderRadius: 6,
                    backgroundColor: gridWorkerStatus === "Running" ? colors.success : (gridWorkerStatus === "Online" ? colors.accent : colors.danger),
                    marginRight: 8
                  }} />
                  <Text style={styles.cardTitle}>{gridWorkerStatus} {gridMetrics?.task && gridMetrics.task !== "Offline" ? `— ${gridMetrics.task}` : ""}</Text>
                </View>
                
                </Panel>

                <Panel>
                  <Text style={styles.sectionTitle}>Operación Actual</Text>
                  {uaoGridStatus ? (
                    <>
                      <View style={{ backgroundColor: "#1E1E1E", padding: 16, borderRadius: 8, borderWidth: 1, borderColor: colors.accent, marginBottom: 16 }}>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 12 }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>Símbolo</Text>
                          <Text style={{ color: colors.text, fontSize: 15, fontWeight: "bold" }}>{uaoGridStatus.symbol || 'N/A'}</Text>
                        </View>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 12 }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>Precio Actual</Text>
                          <Text style={{ color: colors.text, fontSize: 15 }}>{gridMetrics?.last_price || 'N/A'}</Text>
                        </View>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 12 }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>PnL (Flotante)</Text>
                          <Text style={{ color: (uaoGridStatus.pnl ?? 0) >= 0 ? colors.success : colors.danger, fontSize: 15, fontWeight: "bold" }}>
                            ${uaoGridStatus.pnl?.toFixed(4) ?? "0.0000"}
                          </Text>
                        </View>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 12 }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>Posiciones Activas (Grid)</Text>
                          <Text style={{ color: colors.success, fontSize: 15 }}>
                            {gridMetrics?.position_count ?? 0}
                          </Text>
                        </View>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 12 }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>Posición Neta</Text>
                          <Text style={{ color: (uaoGridStatus.net_qty ?? 0) > 0 ? colors.success : colors.danger, fontSize: 15 }}>
                            {uaoGridStatus.net_qty ?? 0}
                          </Text>
                        </View>
                        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                          <Text style={{ color: colors.muted, fontSize: 15 }}>Órdenes Abiertas Totales</Text>
                          <Text style={{ color: colors.warning, fontSize: 15 }}>{uaoGridStatus.open_orders?.length ?? 0}</Text>
                        </View>
                      </View>

                      {uaoGridStatus.open_orders && uaoGridStatus.open_orders.length > 0 && (
                        <View style={{ marginTop: 8 }}>
                          <Text style={[styles.sectionTitle, { fontSize: 14, marginBottom: 8 }]}>Listado de Órdenes (Webhook)</Text>
                          {uaoGridStatus.open_orders.map((order: any, idx: number) => (
                            <View key={idx} style={{ flexDirection: "row", justifyContent: "space-between", backgroundColor: "rgba(255,255,255,0.05)", padding: 10, borderRadius: 6, marginBottom: 6 }}>
                              <Text style={{ color: order.side === "BUY" ? colors.success : colors.danger, fontWeight: "bold" }}>
                                {order.side}
                              </Text>
                              <Text style={{ color: colors.text }}>Precio: {order.price}</Text>
                              <Text style={{ color: colors.muted }}>Cant: {order.qty}</Text>
                            </View>
                          ))}
                        </View>
                      )}
                    </>
                  ) : (
                    <View style={{ backgroundColor: "#1E1E1E", padding: 16, borderRadius: 8 }}>
                      <Text style={{ color: colors.muted, textAlign: "center" }}>Esperando datos de la operación...</Text>
                    </View>
                  )}
                </Panel>
            </>
          )}

          {/* 📊 DASHBOARD 📊 */}
          {screen === "dashboard" && (
            <>
              <Panel>
                <Text style={styles.sectionTitle}>Resumen financiero</Text>
                <Text style={styles.bigNumber}>{totalCapital.toFixed(2)}</Text>
                <Text style={styles.helper}>{balances?.note ?? "Capital consolidado desde OKX."}</Text>
                <View style={styles.inlineButtons}>
                  <Pressable style={styles.secondaryButtonCompact} onPress={() => void loadBalances()}>
                    <Text style={styles.secondaryButtonText}>Refrescar</Text>
                  </Pressable>
                  <Pressable style={styles.secondaryButtonCompact} onPress={() => setScreen("config")}>
                    <Text style={styles.secondaryButtonText}>Bóveda</Text>
                  </Pressable>
                </View>
              </Panel>

              {/* Motor Grid Cuantitativo */}
              {(() => {
                const engine = getFlippingEngine("grid");
                return (
                  <MotorCard
                    title="🤖 Motor Grid Cuantitativo"
                    description="Busca la mejor oportunidad de Grid Trading en todo OKX (Futuros USDT)."
                    active={engine?.enabled ?? false}
                    accentColor={colors.success}
                    rightControl={
                      <Switch
                        value={engine?.enabled ?? false}
                        onValueChange={(v) => void toggleFlippingEngine("grid", v)}
                        trackColor={{ false: "#38506f", true: "#2f6ae6" }}
                        thumbColor="#ffffff"
                      />
                    }
                  >
                    {!engine?.enabled ? (
                      <Text style={styles.helper}>
                        El motor Grid está inactivo. Enciéndelo para que el worker de Python escanee el mercado de forma automática.
                      </Text>
                    ) : (
                      <>
                        <Text style={styles.cardMeta}>Estado: {gridWorkerStatus}</Text>
                        {backtestTop10.length > 0 ? (
                          <View style={{ marginTop: 12, padding: 8, backgroundColor: "rgba(0,255,0,0.05)", borderRadius: 8, borderWidth: 1, borderColor: "rgba(0,255,0,0.2)", gap: 6 }}>
                            <Text style={[styles.cardTitle, { color: colors.success, marginBottom: 2 }]}>🏆 Top 3 Backtest</Text>
                            {backtestTop10.slice(0, 3).map((r: any, i: number) => {
                              const medals = ["🥇", "🥈", "🥉"];
                              return (
                                <View key={r.symbol} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                                  <Text style={{ color: colors.text, fontSize: 13 }}>{medals[i]} {r.symbol}</Text>
                                  <Text style={{ color: r.pnl >= 0 ? colors.success : colors.danger, fontSize: 13, fontWeight: "700" }}>
                                    {r.pnl >= 0 ? "+" : ""}${r.pnl?.toFixed(2)}
                                  </Text>
                                </View>
                              );
                            })}
                          </View>
                        ) : backtestResult ? (
                          <View style={{ marginTop: 12, padding: 8, backgroundColor: "rgba(0,255,0,0.05)", borderRadius: 8, borderWidth: 1, borderColor: "rgba(0,255,0,0.2)" }}>
                            <Text style={[styles.cardTitle, { color: colors.success, marginBottom: 4 }]}>Mejor Oportunidad: {backtestResult.symbol}</Text>
                            <Text style={{ color: colors.muted }}>Ganancia Proyectada: <Text style={{ color: colors.success }}>+${backtestResult.pnl?.toFixed(2)}</Text> | Factor: <Text style={{ color: colors.text }}>{backtestResult.profit_factor?.toFixed(2)}</Text></Text>
                          </View>
                        ) : null}
                      </>
                    )}
                  </MotorCard>
                );
              })()}

              {/* Motor Cripto (Antiguo) */}
              <MotorCard
                title="🪙 Motor Cripto (Arbitraje)"
                description="Arbitraje automático BTC/USDT en OKX."
                active={engineStatus.enabled}
                accentColor={colors.success}
                rightControl={
                  <Switch value={engineStatus.enabled} onValueChange={(v) => void toggleCryptoEngine(v)}
                    trackColor={{ false: "#38506f", true: "#2f6ae6" }} thumbColor="#ffffff" />
                }
              >
                <Text style={styles.cardMeta}>Exchange: {activeExchange} · {activeSandbox ? "sandbox" : "live"}</Text>
                <Text style={styles.cardMeta}>Par: {engineStatus.symbol}</Text>
                <Text style={styles.cardMeta}>
                  Ticker: {engineStatus.lastTicker?.last ?? "–"} | Bid {engineStatus.lastTicker?.bid ?? "–"} | Ask {engineStatus.lastTicker?.ask ?? "–"}
                </Text>
                <Text style={styles.cardMeta}>Última revisión: {engineStatus.lastCheckedAt ?? "pendiente"}</Text>
                {engineStatus.lastError ? <Text style={styles.errorText}>{engineStatus.lastError}</Text> : null}
              </MotorCard>

              {/* Motor Retail Tech (Sprint 3) */}
              {(() => {
                const engine = getFlippingEngine("tech");
                return (
                  <MotorCard
                    title="🖥️ Motor Retail Tech"
                    description="Escanea tiendas de hardware buscando GPUs y servers en descuento."
                    active={engine?.enabled ?? false}
                    accentColor={colors.warning}
                    rightControl={
                      <Switch
                        value={engine?.enabled ?? false}
                        onValueChange={(v) => void toggleFlippingEngine("tech", v)}
                        trackColor={{ false: "#38506f", true: "#2f6ae6" }}
                        thumbColor="#ffffff"
                      />
                    }
                  >
                    <Text style={styles.cardMeta}>
                      Última ejecución: {engine?.lastRunAt ? new Date(engine.lastRunAt).toLocaleString() : "pendiente"}
                    </Text>
                    {engine?.lastError ? <Text style={styles.errorText}>{engine.lastError}</Text> : null}
                    <Pressable onPress={() => setScreen("alerts")}>
                      <Text style={[styles.helper, { color: colors.accent }]}>Ver alertas →</Text>
                    </Pressable>
                  </MotorCard>
                );
              })()}

              {/* Motor Inmobiliario (Sprint 4) */}
              {(() => {
                const engine = getFlippingEngine("real-estate");
                return (
                  <MotorCard
                    title="🏠 Motor Inmobiliario"
                    description="Detecta propiedades infravaloradas usando la regla del 70% con IA."
                    active={engine?.enabled ?? false}
                    accentColor={colors.purple}
                    rightControl={
                      <Switch
                        value={engine?.enabled ?? false}
                        onValueChange={(v) => void toggleFlippingEngine("real-estate", v)}
                        trackColor={{ false: "#38506f", true: "#2f6ae6" }}
                        thumbColor="#ffffff"
                      />
                    }
                  >
                    <Text style={styles.cardMeta}>
                      Última ejecución: {engine?.lastRunAt ? new Date(engine.lastRunAt).toLocaleString() : "pendiente"}
                    </Text>
                    {engine?.lastError ? <Text style={styles.errorText}>{engine.lastError}</Text> : null}
                    <Pressable onPress={() => setScreen("opportunities")}>
                      <Text style={[styles.helper, { color: colors.accent }]}>Ver oportunidades →</Text>
                    </Pressable>
                  </MotorCard>
                );
              })()}

              {/* Motor Micro-SaaS (Sprint 4) */}
              {(() => {
                const engine = getFlippingEngine("saas");
                return (
                  <MotorCard
                    title="💻 Motor Micro-SaaS"
                    description="Analiza negocios digitales subvaluados para adquisición y flip."
                    active={engine?.enabled ?? false}
                    accentColor={colors.accent}
                    rightControl={
                      <Switch
                        value={engine?.enabled ?? false}
                        onValueChange={(v) => void toggleFlippingEngine("saas", v)}
                        trackColor={{ false: "#38506f", true: "#2f6ae6" }}
                        thumbColor="#ffffff"
                      />
                    }
                  >
                    <Text style={styles.cardMeta}>
                      Última ejecución: {engine?.lastRunAt ? new Date(engine.lastRunAt).toLocaleString() : "pendiente"}
                    </Text>
                    {engine?.lastError ? <Text style={styles.errorText}>{engine.lastError}</Text> : null}
                    <Pressable onPress={() => setScreen("opportunities")}>
                      <Text style={[styles.helper, { color: colors.accent }]}>Ver oportunidades →</Text>
                    </Pressable>
                  </MotorCard>
                );
              })()}

              <Panel>
                <Text style={styles.sectionTitle}>Balances detectados</Text>
                {balances?.balances.length ? (
                  balances.balances.map((entry) => (
                    <View key={entry.asset} style={styles.balanceRow}>
                      <Text style={styles.cardTitle}>{entry.asset}</Text>
                      <Text style={styles.cardMeta}>
                        Total {entry.total} · Libre {entry.free} · Usado {entry.used}
                      </Text>
                    </View>
                  ))
                ) : (
                  <Text style={styles.helper}>Sin balances cargados todavía. Guarda tus claves de OKX y refresca.</Text>
                )}
              </Panel>
            </>
          )}
        </ScrollView>
      )}

      {/* ── MODAL: CONFIG GRID ── */}
      <Modal visible={showGridConfig} animationType="slide" transparent onRequestClose={() => setShowGridConfig(false)}>
        <View style={styles.modalOverlay}>
          <View style={[styles.modalSheet, { paddingBottom: 40 }]}>
            <View style={styles.rowBetween}>
              <Text style={styles.sectionTitle}>⚙️ Configuración del Motor</Text>
              <Pressable onPress={() => setShowGridConfig(false)}>
                <Text style={{ color: colors.muted, fontSize: 20, paddingLeft: 12 }}>✕</Text>
              </Pressable>
            </View>

            <View style={{ marginTop: 16, gap: 12 }}>
              <Field label="Capital Base por Grid (USDT)" value={gridBaseCapital} onChangeText={setGridBaseCapital} keyboardType="numeric" />
              <Field label="Apalancamiento Máximo Permitido" value={gridMaxLeverage} onChangeText={setGridMaxLeverage} keyboardType="numeric" />
              <Pressable style={[styles.primaryButton, { marginTop: 8 }]} onPress={() => { void saveGridConfig(); setShowGridConfig(false); }}>
                <Text style={styles.primaryButtonText}>Guardar Configuración</Text>
              </Pressable>
            </View>

            {gridMetrics?.ai_recommendation && (
              <View style={{ marginTop: 24, padding: 12, backgroundColor: "rgba(138,43,226,0.1)", borderRadius: 8, borderWidth: 1, borderColor: "rgba(138,43,226,0.3)" }}>
                <Text style={[styles.sectionTitle, { fontSize: 14, marginBottom: 8, color: "#d0a8f9" }]}>🧠 Parámetros IA Actuales ({gridMetrics.ai_recommendation.source || "LLM"})</Text>
                <Text style={{ color: colors.muted, marginBottom: 4 }}>
                  Leverage Sugerido: <Text style={{ color: colors.text, fontWeight: "700" }}>{gridMetrics.ai_recommendation.leverage}x</Text>
                </Text>
                <Text style={{ color: colors.muted }}>
                  Grid Spacing Factor: <Text style={{ color: colors.text, fontWeight: "700" }}>{gridMetrics.ai_recommendation.grid_spacing_factor}x</Text>
                </Text>
              </View>
            )}
          </View>
        </View>
      </Modal>

      {/* ── MODAL: DETALLE DE OPORTUNIDAD ── */}
      <Modal visible={!!selectedOpportunity} animationType="slide" transparent onRequestClose={() => setSelectedOpportunity(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <ScrollView>
              {selectedOpportunity && (
                <>
                  <View style={styles.rowBetween}>
                    <Text style={[styles.sectionTitle, { flex: 1 }]} numberOfLines={2}>
                      {selectedOpportunity.title}
                    </Text>
                    <Pressable onPress={() => setSelectedOpportunity(null)}>
                      <Text style={{ color: colors.muted, fontSize: 20, paddingLeft: 12 }}>✕</Text>
                    </Pressable>
                  </View>

                  <View style={[styles.scoreChip, { alignSelf: "flex-start", backgroundColor: dealScoreColor(selectedOpportunity.dealScore) + "22" }]}>
                    <Text style={[styles.scoreText, { color: dealScoreColor(selectedOpportunity.dealScore) }]}>
                      ⭐ Deal Score: {selectedOpportunity.dealScore ?? "–"}/10
                    </Text>
                  </View>

                  <Text style={[styles.cardMeta, { marginTop: 12 }]}>{selectedOpportunity.description}</Text>

                  {selectedOpportunity.estimatedValue ? (
                    <View style={styles.infoBlock}>
                      <Text style={styles.infoLabel}>💰 Valor estimado</Text>
                      <Text style={[styles.cardTitle, { color: colors.success }]}>{selectedOpportunity.estimatedValue}</Text>
                    </View>
                  ) : null}

                  {selectedOpportunity.estimatedRepair ? (
                    <View style={styles.infoBlock}>
                      <Text style={styles.infoLabel}>🔧 Reparaciones estimadas</Text>
                      <Text style={styles.cardMeta}>{selectedOpportunity.estimatedRepair}</Text>
                    </View>
                  ) : null}

                  <View style={styles.infoBlock}>
                    <Text style={styles.infoLabel}>🤖 Análisis IA</Text>
                    <Text style={styles.cardMeta}>{selectedOpportunity.aiAnalysis}</Text>
                  </View>

                  {selectedOpportunity.sourceUrl ? (
                    <View style={styles.infoBlock}>
                      <Text style={styles.infoLabel}>🔗 Fuente</Text>
                      <Pressable onPress={() => Linking.openURL(selectedOpportunity.sourceUrl as string).catch(() => {})}>
                        <Text style={[styles.helper, { color: colors.accent, textDecorationLine: 'underline' }]}>{selectedOpportunity.sourceUrl}</Text>
                      </Pressable>
                    </View>
                  ) : null}

                  <View style={styles.tagRow}>
                    {(selectedOpportunity.tags ?? []).map((tag) => (
                      <View key={tag} style={styles.tag}>
                        <Text style={styles.tagText}>{tag}</Text>
                      </View>
                    ))}
                  </View>

                  <View style={[styles.inlineButtons, { marginTop: 16 }]}>
                    {selectedOpportunity.status !== "reviewed" && (
                      <Pressable
                        style={[styles.secondaryButtonCompact, { flex: 1 }]}
                        onPress={() => void updateOpportunityStatus(selectedOpportunity.id, "reviewed")}
                      >
                        <Text style={styles.secondaryButtonText}>✅ Revisar</Text>
                      </Pressable>
                    )}
                    {selectedOpportunity.status !== "archived" && (
                      <Pressable
                        style={[styles.secondaryButtonCompact, { flex: 1 }]}
                        onPress={() => void updateOpportunityStatus(selectedOpportunity.id, "archived")}
                      >
                        <Text style={styles.secondaryButtonText}>📦 Archivar</Text>
                      </Pressable>
                    )}
                  </View>
                </>
              )}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {loading ? (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color={colors.text} />
        </View>
      ) : null}
    </View>
  );
}

// ─── Sub-componentes ─────────────────────────────────────────────────────────

function Panel({ children }: { children: React.ReactNode }) {
  return <View style={styles.panel}>{children}</View>;
}

function TabButton({ active, label, onPress }: { active: boolean; label: string; onPress: () => void }) {
  return (
    <Pressable style={[styles.tabButton, active && styles.tabButtonActive]} onPress={onPress}>
      <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>{label}</Text>
    </Pressable>
  );
}

function Field({
  label, value, onChangeText, secureTextEntry, autoCapitalize, keyboardType
}: {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  secureTextEntry?: boolean;
  autoCapitalize?: "none" | "sentences" | "words" | "characters";
  keyboardType?: "default" | "numeric" | "email-address" | "phone-pad";
}) {
  const handleFocus = () => {
    // Si el valor es un placeholder enmascarado, limpiar al hacer focus
    if (value.includes("••••")) {
      onChangeText("");
    }
  };

  return (
    <View style={styles.fieldGroup}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        onFocus={handleFocus}
        secureTextEntry={secureTextEntry}
        autoCapitalize={autoCapitalize}
        keyboardType={keyboardType}
        style={[styles.input, value.includes("••••") && { color: colors.success }]}
        placeholder={label}
        placeholderTextColor={colors.muted}
      />
    </View>
  );
}

function RowBetween({ left, right }: { left: string; right: React.ReactNode }) {
  return (
    <View style={styles.rowBetween}>
      <Text style={styles.fieldLabel}>{left}</Text>
      {right}
    </View>
  );
}

function MotorCard({
  title, description, active, accentColor, rightControl, children,
}: {
  title: string;
  description: string;
  active: boolean;
  accentColor: string;
  rightControl?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <View style={[styles.panel, { borderColor: active ? accentColor : colors.border }]}>
      <View style={styles.rowBetween}>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{title}</Text>
          <Text style={styles.cardMeta}>{description}</Text>
        </View>
        {rightControl ?? (
          <Text style={[styles.badge, { color: active ? colors.success : colors.muted }]}>
            {active ? "ON" : "OFF"}
          </Text>
        )}
      </View>
      {children}
    </View>
  );
}

// ─── Estilos ─────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.bg },
  loginContainer: { padding: 24, gap: 16 },
  appContainer: { padding: 20, gap: 16 },
  topBar: { gap: 14 },
  brand: { color: colors.text, fontSize: 28, fontWeight: "700" },
  eyebrow: { color: colors.accent, fontSize: 14, fontWeight: "700", marginTop: 32 },
  title: { color: colors.text, fontSize: 30, fontWeight: "700", lineHeight: 38 },
  subtitle: { color: colors.muted, fontSize: 15, lineHeight: 22 },
  welcome: { color: colors.muted, fontSize: 14 },
  tabRow: { flexDirection: "row", gap: 8 },
  tabButton: {
    paddingHorizontal: 14, paddingVertical: 10,
    borderRadius: 999, backgroundColor: colors.panel,
    borderWidth: 1, borderColor: colors.border,
  },
  tabButtonActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  tabLabel: { color: colors.muted, fontWeight: "600" },
  tabLabelActive: { color: "#ffffff" },
  panel: {
    backgroundColor: colors.panel, borderWidth: 1,
    borderColor: colors.border, borderRadius: 20, padding: 18, gap: 14,
  },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: "700" },
  helper: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  fieldGroup: { gap: 8 },
  fieldLabel: { color: colors.text, fontSize: 14, fontWeight: "600" },
  input: {
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.panelAlt,
    borderRadius: 14, paddingHorizontal: 14, paddingVertical: 12, color: colors.text,
  },
  primaryButton: { backgroundColor: colors.accent, paddingVertical: 14, borderRadius: 14, alignItems: "center" },
  primaryButtonText: { color: "#ffffff", fontWeight: "700" },
  secondaryButton: {
    backgroundColor: "transparent", borderWidth: 1, borderColor: colors.border,
    paddingVertical: 14, borderRadius: 14, alignItems: "center",
  },
  secondaryButtonCompact: {
    backgroundColor: "transparent", borderWidth: 1, borderColor: colors.border,
    paddingVertical: 12, paddingHorizontal: 14, borderRadius: 12, alignItems: "center",
  },
  dangerButton: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "rgba(231, 76, 60, 0.2)",
    borderWidth: 1,
    borderColor: "rgba(231, 76, 60, 0.5)",
    alignItems: "center",
  },
  secondaryButtonText: { color: colors.text, fontWeight: "600" },
  inlineButtons: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  bigNumber: { color: colors.text, fontSize: 36, fontWeight: "700" },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 },
  cardTitle: { color: colors.text, fontSize: 16, fontWeight: "700" },
  cardMeta: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  badge: { fontSize: 13, fontWeight: "700" },
  balanceRow: { gap: 4, paddingBottom: 8, borderBottomWidth: 1, borderBottomColor: colors.border },
  credentialRow: { gap: 4, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.border },
  errorText: { color: colors.danger, fontSize: 13 },
  loadingOverlay: {
    position: "absolute", top: 0, right: 0, bottom: 0, left: 0,
    backgroundColor: "rgba(8, 17, 31, 0.45)", alignItems: "center", justifyContent: "center",
  },
  // Sprint 3 & 4
  unreadDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.danger },
  scoreChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, alignItems: "center" },
  scoreText: { fontSize: 13, fontWeight: "700" },
  tagRow: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
  tag: { backgroundColor: colors.panelAlt, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  tagText: { color: colors.muted, fontSize: 12 },
  // Modal
  modalOverlay: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.6)",
    justifyContent: "flex-end",
  },
  modalSheet: {
    backgroundColor: colors.panel, borderTopLeftRadius: 28, borderTopRightRadius: 28,
    padding: 24, gap: 14, maxHeight: "90%",
  },
  infoBlock: { gap: 6, paddingTop: 8, borderTopWidth: 1, borderTopColor: colors.border },
  infoLabel: { color: colors.muted, fontSize: 12, fontWeight: "600", textTransform: "uppercase" },
});
