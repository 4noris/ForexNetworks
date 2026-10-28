import { useEffect } from "react";
import { useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { watchLiveData } from "../lib/liveTransport";
import {
  getDashboard,
  getMarketWatch,
  getAccounts,
  getRiskAutomationOverview,
  getManagerStatus,
  operationsWebSocketUrl,
} from "../lib/api";

export const MARKET_WATCH_REFETCH_INTERVAL_MS = 1000;

function useStreamedQuery(config, path) {
  const client = useQueryClient();
  const queryKey = JSON.stringify(config.queryKey);
  const enabled = config.enabled !== false;
  useEffect(() => {
    if (!enabled || import.meta.env.VITE_LIVE_STREAM === "false") return undefined;
    return watchLiveData(path, {}, (data) => client.setQueryData(JSON.parse(queryKey), data),
      operationsWebSocketUrl().replace("/api/operations/ws", "/api/live/ws"));
  }, [client, queryKey, path, enabled]);
  // Existing polling also keeps subscriptions active and is the fallback when
  // sockets are blocked. apiRequest serves the streamed value while healthy.
  return useQuery(config);
}

export function useManagerStatus() {
  return useQuery({
    queryKey: ["manager-status"],
    queryFn: getManagerStatus,
    refetchInterval: 3000,
    staleTime: 2000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}

export function useDashboard(enabled) {
  return useStreamedQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    enabled,
    // The backend publishes atomic Manager generations every 1-2 seconds.
    // Poll the published generation every three seconds and retain the
    // previous one while a new snapshot is in flight.
    refetchInterval: 3000,
    staleTime: 2000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  }, "/api/manager/dashboard");
}

export function useMarketWatch(search, enabled, limit = 250) {
  return useStreamedQuery({
    queryKey: ["market-watch", search, limit],
    queryFn: () => getMarketWatch({ q: search, limit }),
    enabled,
    refetchInterval: MARKET_WATCH_REFETCH_INTERVAL_MS,
    staleTime: 500,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  }, `/api/manager/market-watch?${new URLSearchParams({ q: search || "", limit: String(limit) })}`);
}

export function useAccounts(params, enabled) {
  return useStreamedQuery({
    queryKey: ["accounts", params],
    queryFn: () => getAccounts(params),
    enabled,
    refetchInterval: 2000,
    staleTime: 1500,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  }, `/api/manager/accounts?${new URLSearchParams({ q: params?.q || "", enabled_only: String(Boolean(params?.enabled_only)) })}`);
}

export function useRiskAutomationOverview(
  enabled,
  limit = 250,
  { includeClients = false, includeAudit = false, clientLimit = 500 } = {},
) {
  return useQuery({
    queryKey: [
      "risk-automation-overview",
      limit,
      includeClients,
      includeAudit,
      clientLimit,
    ],
    queryFn: () =>
      getRiskAutomationOverview(limit, {
        includeClients,
        includeAudit,
        clientLimit,
      }),
    enabled,
    refetchInterval: 5000,
    staleTime: 3000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    placeholderData: keepPreviousData,
  });
}

export function useWarmManagerData(enabled) {
  useDashboard(enabled);
  useMarketWatch("", enabled, 100);
  useRiskAutomationOverview(enabled, 250);
}
