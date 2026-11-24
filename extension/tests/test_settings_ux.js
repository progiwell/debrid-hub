const assert = require("assert");

console.log("Running settings_ux tests...");

// Test helper: Settings UX state machine model
function computeSettingsVisibility({ endpoints, activeEndpointId, settingsMode }) {
  const activeProfile = endpoints.find((ep) => ep.id === activeEndpointId) || endpoints[0] || null;
  const hasConfiguredServer = Boolean(activeProfile && activeProfile.url);
  const isMultiServer = endpoints.length > 1;

  const effectiveMode = !hasConfiguredServer ? "edit" : settingsMode;

  return {
    profilesSectionVisible: isMultiServer,
    addServerSingleWrapVisible: !isMultiServer && hasConfiguredServer && effectiveMode === "view",
    activeServerCardVisible: effectiveMode === "view" && hasConfiguredServer,
    serverEditFormVisible: effectiveMode !== "view" || !hasConfiguredServer,
    cancelEditBtnVisible: Boolean(hasConfiguredServer && effectiveMode !== "view"),
    profileNameGroupVisible: isMultiServer || effectiveMode === "add",
    activeProfileName: activeProfile?.name || "Default",
    activeProfileUrl: activeProfile?.url || "",
  };
}

// 1. Test clean install (0 servers configured)
{
  const state = computeSettingsVisibility({
    endpoints: [],
    activeEndpointId: "",
    settingsMode: "view",
  });

  assert.strictEqual(state.profilesSectionVisible, false, "Profiles section should be hidden when 0 servers");
  assert.strictEqual(state.addServerSingleWrapVisible, false, "+ Add Server link should be hidden when 0 servers");
  assert.strictEqual(state.activeServerCardVisible, false, "Active server card should be hidden when 0 servers");
  assert.strictEqual(state.serverEditFormVisible, true, "Edit form should be visible on fresh install");
  assert.strictEqual(state.cancelEditBtnVisible, false, "Cancel button should be hidden when no server configured");
  assert.strictEqual(state.profileNameGroupVisible, false, "Server Name input should be hidden on fresh install");
}

// 2. Test single-server configured in view mode
{
  const state = computeSettingsVisibility({
    endpoints: [{ id: "default", name: "Default", url: "http://100.64.0.10:38415", apiKey: "" }],
    activeEndpointId: "default",
    settingsMode: "view",
  });

  assert.strictEqual(state.profilesSectionVisible, false, "Profiles section should be hidden for single-server");
  assert.strictEqual(state.addServerSingleWrapVisible, true, "+ Add Server link should be visible in view mode");
  assert.strictEqual(state.activeServerCardVisible, true, "Active server card should be visible in view mode");
  assert.strictEqual(state.serverEditFormVisible, false, "Edit form should be hidden in view mode");
  assert.strictEqual(state.activeProfileName, "Default", "Default name should be 'Default'");
  assert.strictEqual(state.activeProfileUrl, "http://100.64.0.10:38415");
}

// 3. Test single-server clicking 'Edit'
{
  const state = computeSettingsVisibility({
    endpoints: [{ id: "default", name: "Default", url: "http://100.64.0.10:38415", apiKey: "" }],
    activeEndpointId: "default",
    settingsMode: "edit",
  });

  assert.strictEqual(state.profilesSectionVisible, false, "Profiles section should be hidden for single-server");
  assert.strictEqual(state.addServerSingleWrapVisible, false, "+ Add Server link should be hidden during edit");
  assert.strictEqual(state.activeServerCardVisible, false, "Active server card should be hidden during edit");
  assert.strictEqual(state.serverEditFormVisible, true, "Edit form should be visible during edit");
  assert.strictEqual(state.cancelEditBtnVisible, true, "Cancel button should be visible during edit");
  assert.strictEqual(state.profileNameGroupVisible, false, "Server Name input should be hidden for single-server edit");
}

// 4. Test single-server clicking '+ Add Server'
{
  const state = computeSettingsVisibility({
    endpoints: [{ id: "default", name: "Default", url: "http://100.64.0.10:38415", apiKey: "" }],
    activeEndpointId: "default",
    settingsMode: "add",
  });

  assert.strictEqual(state.profilesSectionVisible, false, "Profiles list hidden until saved");
  assert.strictEqual(state.activeServerCardVisible, false, "Active card hidden during add");
  assert.strictEqual(state.serverEditFormVisible, true, "Edit form visible during add");
  assert.strictEqual(state.cancelEditBtnVisible, true, "Cancel button visible during add");
  assert.strictEqual(state.profileNameGroupVisible, true, "Server Name input visible when adding new server");
}

// 5. Test multi-server configured in view mode
{
  const state = computeSettingsVisibility({
    endpoints: [
      { id: "default", name: "Default", url: "http://100.64.0.10:38415", apiKey: "" },
      { id: "nas", name: "Remote NAS", url: "http://192.168.1.100:8080", apiKey: "secret" },
    ],
    activeEndpointId: "default",
    settingsMode: "view",
  });

  assert.strictEqual(state.profilesSectionVisible, true, "Profiles section visible when multi-server");
  assert.strictEqual(state.addServerSingleWrapVisible, false, "Single-server link hidden when multi-server");
  assert.strictEqual(state.activeServerCardVisible, true, "Active server card visible in view mode");
  assert.strictEqual(state.serverEditFormVisible, false, "Edit form hidden in view mode");
}

// 6. Test multi-server configured in edit mode
{
  const state = computeSettingsVisibility({
    endpoints: [
      { id: "default", name: "Default", url: "http://100.64.0.10:38415", apiKey: "" },
      { id: "nas", name: "Remote NAS", url: "http://192.168.1.100:8080", apiKey: "secret" },
    ],
    activeEndpointId: "nas",
    settingsMode: "edit",
  });

  assert.strictEqual(state.profilesSectionVisible, true, "Profiles section remains visible when multi-server");
  assert.strictEqual(state.serverEditFormVisible, true, "Edit form visible");
  assert.strictEqual(state.profileNameGroupVisible, true, "Server Name input visible in multi-server edit");
  assert.strictEqual(state.cancelEditBtnVisible, true, "Cancel button visible");
  assert.strictEqual(state.activeProfileName, "Remote NAS");
}

console.log("✅ All settings_ux tests passed!");
