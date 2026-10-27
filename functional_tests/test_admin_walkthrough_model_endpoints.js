// test_admin_walkthrough_model_endpoints.js
// Version: 0.261.032
// Implemented in: 0.261.032
// Exercises multi-endpoint and legacy model validation in the setup walkthrough.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '..', 'application', 'single_app', 'static', 'js', 'admin', 'admin_settings.js'),
    'utf8'
);

function extractFunction(name) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `Missing ${name}`);
    const end = source.indexOf('\n}', start);
    assert.notEqual(end, -1, `Missing end of ${name}`);
    return source.slice(start, end + 2);
}

const names = [
    'isWalkthroughMultiEndpointEnabled',
    'getWalkthroughModelEndpoints',
    'isWalkthroughEndpointConfigured',
    'hasWalkthroughEnabledModel',
    'isStepComplete',
    'isFirstTimeSetup'
];
const elements = {
    enable_gpt_apim: { checked: false },
    azure_openai_gpt_endpoint: { value: '' },
    azure_openai_gpt_authentication_type: { value: 'managed_identity' },
    azure_openai_gpt_key: { value: '' },
    azure_apim_gpt_endpoint: { value: '' },
    azure_apim_gpt_subscription_key: { value: '' },
    azure_apim_gpt_deployment: { value: '' },
    model_endpoints_json: { value: '' }
};
const errors = [];
const context = vm.createContext({
    document: { getElementById: id => elements[id] || null },
    window: { enableMultiModelEndpoints: true, modelEndpoints: [] },
    console: { error: (...args) => errors.push(args) },
    gptSelected: [],
    embeddingSelected: []
});
vm.runInContext(names.map(extractFunction).join('\n'), context);

const model = { enabled: true, deploymentName: 'chat-model' };
const endpoint = { enabled: true, connection: { endpoint: 'https://example.test' }, models: [model] };
const setEndpoints = endpoints => { elements.model_endpoints_json.value = JSON.stringify(endpoints); };

assert.equal(context.isStepComplete(2), false);
assert.equal(context.isStepComplete(3), false);
setEndpoints([endpoint]);
assert.equal(context.isStepComplete(2), true);
assert.equal(context.isStepComplete(3), true);
assert.equal(context.isFirstTimeSetup(), false);
setEndpoints([{ ...endpoint, enabled: false }]);
assert.equal(context.isStepComplete(2), false);
assert.equal(context.isStepComplete(3), false);
setEndpoints([{ ...endpoint, models: [{ ...model, enabled: false }] }]);
assert.equal(context.isStepComplete(2), true);
assert.equal(context.isStepComplete(3), false);
setEndpoints([{ ...endpoint, models: [{ enabled: true, deploymentName: ' ' }] }]);
assert.equal(context.isStepComplete(3), false);
setEndpoints([{ ...endpoint, connection: { endpoint: ' ' } }]);
assert.equal(context.isStepComplete(2), false);
assert.equal(context.isStepComplete(3), false);
setEndpoints([{ ...endpoint, models: [] }, { ...endpoint, enabled: false }]);
assert.equal(context.isStepComplete(3), false);
setEndpoints([{ ...endpoint, models: [{ enabled: true, modelName: 'custom-model' }] }]);
assert.equal(context.isStepComplete(3), true);
setEndpoints([{ ...endpoint, auth: { type: 'api_key' } }]);
assert.equal(context.isStepComplete(2), false);
setEndpoints([{ ...endpoint, auth: { type: 'api_key' }, has_api_key: true }]);
assert.equal(context.isStepComplete(3), true);
setEndpoints([{ ...endpoint, auth: { type: 'service_principal', tenant_id: 'tenant', client_id: 'client' } }]);
assert.equal(context.isStepComplete(2), false);
setEndpoints([{ ...endpoint, auth: { type: 'service_principal', tenant_id: 'tenant', client_id: 'client' }, has_client_secret: true }]);
assert.equal(context.isStepComplete(3), true);
elements.model_endpoints_json.value = '{invalid';
assert.equal(context.isStepComplete(2), false);
assert.equal(errors.length, 1);
elements.model_endpoints_json.value = '';
context.window.modelEndpoints = [endpoint];
assert.equal(context.isStepComplete(3), true);

elements.enable_multi_model_endpoints = { checked: false };
assert.equal(context.isStepComplete(2), false);
elements.azure_openai_gpt_endpoint.value = 'https://example.test';
assert.equal(context.isStepComplete(2), true);
elements.azure_openai_gpt_authentication_type.value = 'key';
assert.equal(context.isStepComplete(2), false);
elements.azure_openai_gpt_key.value = 'test-key';
assert.equal(context.isStepComplete(2), true);
assert.equal(context.isStepComplete(3), false);
context.gptSelected.push({ deploymentName: 'legacy-model' });
assert.equal(context.isStepComplete(3), true);
elements.enable_gpt_apim.checked = true;
assert.equal(context.isStepComplete(2), false);
elements.azure_apim_gpt_endpoint.value = 'https://example.test';
elements.azure_apim_gpt_subscription_key.value = 'test-key';
assert.equal(context.isStepComplete(2), true);
assert.ok(!context.isStepComplete(3));
elements.azure_apim_gpt_deployment.value = 'deployment';
assert.equal(context.isStepComplete(3), true);
elements.enable_multi_model_endpoints.checked = true;
assert.equal(context.isStepComplete(3), true);

console.log('Walkthrough model endpoint validation passed.');
