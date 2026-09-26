// Re-register the original read-only observer after each Director/round setup.
// This addon does not set Director options, spawn combat objects or change rules.
EntFire("worldspawn", "RunScriptFile", "astra_runtime_start", 0.5);
