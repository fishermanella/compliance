import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { execSync } from 'child_process';
import { getReportBuffer, createWrappedFetch, ensureSupabaseEnvironment, projectContext } from 'coze-coding-dev-sdk';

let envLoaded = false;
let projectCredentials: SupabaseCredentials | undefined;

interface SupabaseCredentials {
  url: string;
  anonKey: string;
}

async function loadEnv(): Promise<void> {
  if (envLoaded) {
    return;
  }

  // Project runtime resolves public resource values from Integration and holds
  // them only in this process. The user token comes from process environment.
  const context = projectContext();
  if (context) {
    const resolved = await ensureSupabaseEnvironment();
    if (!resolved) throw new Error('Supabase project configuration is unavailable');
    projectCredentials = { url: resolved.supabaseUrl, anonKey: resolved.anonKey };
    envLoaded = true;
    return;
  }

  // Compatibility path for non-plugin projects.
  if (process.env.COZE_SUPABASE_URL && process.env.COZE_SUPABASE_ANON_KEY) {
    envLoaded = true;
    return;
  }

  try {
    try {
      const { config } = await import('dotenv');
      config();
      if (process.env.COZE_SUPABASE_URL && process.env.COZE_SUPABASE_ANON_KEY) {
        envLoaded = true;
        return;
      }
    } catch {
      // dotenv not available
    }

    const pythonCode = `
import os
import sys
try:
    from coze_workload_identity import Client
    client = Client()
    env_vars = client.get_project_env_vars()
    client.close()
    for env_var in env_vars:
        print(f"{env_var.key}={env_var.value}")
except Exception as e:
    print(f"# Error: {e}", file=sys.stderr)
`;

    const output = execSync(`python3 -c '${pythonCode.replace(/'/g, "'\"'\"'")}'`, {
      encoding: 'utf-8',
      timeout: 10000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    const lines = output.trim().split('\n');
    for (const line of lines) {
      if (line.startsWith('#')) continue;
      const eqIndex = line.indexOf('=');
      if (eqIndex > 0) {
        const key = line.substring(0, eqIndex);
        let value = line.substring(eqIndex + 1);
        if ((value.startsWith("'") && value.endsWith("'")) ||
            (value.startsWith('"') && value.endsWith('"'))) {
          value = value.slice(1, -1);
        }
        if (!process.env[key]) {
          process.env[key] = value;
        }
      }
    }

    envLoaded = true;
  } catch {
    // Silently fail
  }
}

async function getSupabaseCredentials(): Promise<SupabaseCredentials> {
  await loadEnv();

  if (projectCredentials) return projectCredentials;

  const url = process.env.COZE_SUPABASE_URL;
  const anonKey = process.env.COZE_SUPABASE_ANON_KEY;

  if (!url) {
    throw new Error('COZE_SUPABASE_URL is not set');
  }
  if (!anonKey) {
    throw new Error('COZE_SUPABASE_ANON_KEY is not set');
  }

  return { url, anonKey };
}

async function getSupabaseServiceRoleKey(): Promise<string | undefined> {
  await loadEnv();
  if (projectContext()) return undefined;
  return process.env.COZE_SUPABASE_SERVICE_ROLE_KEY;
}

async function getSupabaseClient(token?: string): Promise<SupabaseClient> {
  const { url, anonKey } = await getSupabaseCredentials();

  let key: string;
  if (token) {
    key = anonKey;
  } else {
    const serviceRoleKey = await getSupabaseServiceRoleKey();
    key = serviceRoleKey ?? anonKey;
  }

  const globalOptions: {
    headers?: { Authorization: string };
    fetch?: typeof fetch;
  } = {};
  if (token) {
    globalOptions.headers = { Authorization: `Bearer ${token}` };
  }
  try {
    const buffer = getReportBuffer();
    if (buffer) {
      globalOptions.fetch = createWrappedFetch(buffer, 'supabase');
    }
  } catch (e) {
    console.error('[report] supabase-client: setup failed:', e);
  }

  return createClient(url, key, {
    global: globalOptions,
    db: {
      timeout: 60000,
    },
    auth: {
      autoRefreshToken: false,
      persistSession: false,
    },
  });
}

export { loadEnv, getSupabaseCredentials, getSupabaseServiceRoleKey, getSupabaseClient };
