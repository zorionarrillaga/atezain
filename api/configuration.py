"""One policy configuration shared by serving and current-workflow evaluation."""
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from policy import PolicyConfig
from policy.model import ActionSpec


def served_policy(mode, adapter='invoices-es'):
    root=Path(__file__).resolve().parents[1]
    config=PolicyConfig.load(root/'adapters'/adapter/'permissions.toml')
    if mode in {'pilot','enterprise'}:
        config=config.replace(actions={name:replace(spec,approval='required') for name,spec in config.actions.items()})
    if mode=='enterprise':
        config=config.replace(daily_writes=300,actions={name:replace(spec,daily_max=100) if name=='send_reminder' else spec for name,spec in config.actions.items()})
    return config.replace(actions={**config.actions,'manage_case':ActionSpec('manage_case','invoice',('case_json',),'required',False,None,MappingProxyType({}))})


def served_llm(model, api_key=None):
    from agent.llm import GroqLLM
    effort = 'low' if model in {'openai/gpt-oss-120b','openai/gpt-oss-20b'} else None
    return GroqLLM(model=model, api_key=api_key, max_output_tokens=2048, json_mode=True, reasoning_effort=effort)
