import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {transform} from 'esbuild';
const source=readFileSync('src/lib/provider-protocol-options.ts','utf8');
const {code}=await transform(source,{loader:'ts',format:'esm'});
const {supportedApiTypeOptions}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
for(const model of ['deepseek-flash','deepseek-v4-flash','deepseek-v4-pro']) {
 assert.deepEqual(supportedApiTypeOptions('deepseek',model),['auto','responses','messages','chat_completions'],model);
}
assert.deepEqual(supportedApiTypeOptions('deepseek','deepseek-auto'),['auto']);
assert.deepEqual(supportedApiTypeOptions('deepseek','unknown'),['auto','chat_completions']);
console.log('DeepSeek protocol options: supported model names and auto mode passed');

for (const [provider, options] of Object.entries({openai:["auto","responses","chat_completions"],anthropic:["auto","messages"],gemini:["auto","generate_content"],vertexai:["auto","generate_content"],custom:["auto","responses","chat_completions","messages","generate_content"],openrouter:["auto","chat_completions"],ollama:["auto","chat_completions"],chatgpt:["auto","responses"]})) assert.deepEqual(supportedApiTypeOptions(provider,"user-model"),options,provider);
