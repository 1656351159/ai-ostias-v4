# -*- coding: utf-8 -*-
"""
LLM 工厂模块 - 统一管理所有模型配置与实例创建

支持的协议:
  - Anthropic 协议: Claude, Kimi (ChatAnthropic)
  - OpenAI 兼容协议: Doubao, GPT, DeepSeek, MiniMax (ChatOpenAI)

使用方式:
  # 方式1: 从环境变量自动创建
  llm, config = create_llm_from_env()

  # 方式2: 手动指定参数
  llm, config = create_llm(
      model_name="doubao-seed-2-0-pro-260215",
      base_url="https://ark.cn-beijing.volces.com/api/v3",
      api_key="your-api-key",
  )

  # 方式3: 分步获取配置后创建
  config = resolve_model_config()
  llm = build_llm_instance(config)
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _import_langchain():
    """延迟导入 langchain 适配层。

    mock 爬取模式（USE_MOCK_CRAWLER=true）不需要 langchain，
    只有真实创建 LLM 实例时才要求这两个包已安装。
    """
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    return ChatOpenAI, ChatAnthropic


# ============================================================
# 模型配置数据类
# ============================================================

@dataclass
class LLMConfig:
    """统一的模型配置"""
    raw_model: str           # 原始模型名称
    fixed_model: str         # 带 provider 前缀的修正名称
    pure_model: str          # 纯模型名称 (去掉前缀)
    base_url: str            # 修正后的 Base URL
    api_key: str             # API Key
    protocol: str            # 协议: "anthropic" / "openai"
    temperature: float = 0.2
    max_tokens: int = 4096

    @property
    def api_key_masked(self) -> str:
        """返回脱敏后的 API Key"""
        if len(self.api_key) > 8:
            return self.api_key[:4] + '...' + self.api_key[-4:]
        return '***'


# ============================================================
# 模型名称 & Base URL 修正工具
# ============================================================

_MODEL_PREFIXES = ('openai/', 'anthropic/', 'minimax/', 'huggingface/')


def fix_model_name(model_name: str) -> str:
    """
    自动修正模型名称，添加正确的 provider 前缀。

    判断逻辑:
      - minimax/abab  → minimax/
      - doubao/gpt    → openai/
      - claude        → anthropic/
      - kimi          → anthropic/  (Kimi 使用 Anthropic 协议)
      - deepseek      → openai/
    """
    if not model_name:
        return model_name

    # 已有前缀则直接返回
    if model_name.startswith(_MODEL_PREFIXES):
        return model_name

    model_lower = model_name.lower()

    if 'minimax' in model_lower or 'abab' in model_lower:
        return f"minimax/{model_name}"
    elif 'doubao' in model_lower or 'gpt' in model_lower:
        return f"openai/{model_name}"
    elif 'claude' in model_lower:
        return f"anthropic/{model_name}"
    elif 'kimi' in model_lower:
        return f"anthropic/{model_name}"
    elif 'deepseek' in model_lower:
        return f"openai/{model_name}"

    return model_name


def fix_base_url(base_url: str, model_name: str) -> str:
    """
    根据模型类型修正 Base URL。

    特殊处理:
      - Kimi: 去掉 /v1 后缀 (ChatAnthropic 自动拼接 /v1/messages)
      - MiniMax: 去掉 /anthropic 路径片段, 确保以 /v1 结尾
      - OpenAI 兼容 (doubao/gpt/deepseek): 确保以 /v1 结尾
      - Claude: 不做特殊处理
    """
    if not base_url:
        return base_url

    base_url = base_url.rstrip('/')
    model_lower = model_name.lower()

    if 'kimi' in model_lower:
        if base_url.endswith('/v1'):
            base_url = base_url[:-3]
        return base_url

    if 'minimax' in model_lower or 'abab' in model_lower:
        if 'anthropic' in base_url:
            base_url = base_url.replace('/anthropic', '')
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        return base_url

    if any(x in model_lower for x in ['doubao', 'gpt', 'deepseek']):
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        return base_url

    if 'claude' in model_lower:
        return base_url

    return base_url


def extract_pure_model_name(fixed_model_name: str) -> str:
    """从带前缀的模型名称中提取纯模型名称"""
    if '/' in fixed_model_name:
        return fixed_model_name.split('/')[-1]
    return fixed_model_name


def detect_protocol(fixed_model_name: str) -> str:
    """
    检测模型使用的协议类型。
    返回 "anthropic" 或 "openai"。
    """
    model_lower = fixed_model_name.lower()
    if 'anthropic' in model_lower or 'claude' in model_lower or 'kimi' in model_lower:
        return "anthropic"
    return "openai"


# ============================================================
# 配置解析
# ============================================================

def resolve_model_config(
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> LLMConfig:
    """
    解析并修正模型配置，返回统一的 LLMConfig。

    Args:
        model_name:  模型名称, 默认从 AI_MODEL_NAME 环境变量获取
        base_url:    API 地址, 默认从 AI_BASE_URL 环境变量获取
        api_key:     API Key, 默认从 AI_API_KEY 环境变量获取
        temperature: 温度参数
        max_tokens:  最大 token 数

    Returns:
        LLMConfig 实例

    Raises:
        ValueError: 缺少必要的 api_key 或 base_url
    """
    # 从环境变量读取未提供的参数
    raw_model = model_name or os.getenv("AI_MODEL_NAME", "openai/doubao-seed-2-0-pro-260215")
    resolved_base_url = base_url or os.getenv("AI_BASE_URL")
    resolved_api_key = api_key or os.getenv("AI_API_KEY")

    if not resolved_api_key:
        raise ValueError("AI_API_KEY is required (set in environment or pass explicitly)")

    if not resolved_base_url:
        raise ValueError("AI_BASE_URL is required (set in environment or pass explicitly)")

    # 修正模型名称和 Base URL
    fixed_model = fix_model_name(raw_model)
    fixed_base_url = fix_base_url(resolved_base_url, fixed_model)
    pure_model = extract_pure_model_name(fixed_model)
    protocol = detect_protocol(fixed_model)

    config = LLMConfig(
        raw_model=raw_model,
        fixed_model=fixed_model,
        pure_model=pure_model,
        base_url=fixed_base_url,
        api_key=resolved_api_key,
        protocol=protocol,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    logger.info(
        "[LLMFactory] 配置解析完成: raw=%s → fixed=%s, protocol=%s, base_url=%s",
        raw_model, fixed_model, protocol, fixed_base_url,
    )

    return config


# ============================================================
# LLM 实例构建 (工厂核心)
# ============================================================

def build_llm_instance(config: LLMConfig) -> Tuple[object, LLMConfig]:
    """
    根据 LLMConfig 创建对应的 LLM 实例。

    Args:
        config: 解析后的模型配置

    Returns:
        (llm_instance, config) 元组
    """
    ChatOpenAI, ChatAnthropic = _import_langchain()

    if config.protocol == "anthropic":
        llm = ChatAnthropic(
            model=config.pure_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers={"anthropic-version": "2023-06-01"},
        )
        logger.info("[LLMFactory] 创建 ChatAnthropic: model=%s, base_url=%s", config.pure_model, config.base_url)

    elif 'minimax' in config.fixed_model.lower() or 'abab' in config.fixed_model.lower():
        # MiniMax 使用 OpenAI 兼容接口，需要特殊 header
        llm = ChatOpenAI(
            model=config.pure_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers={"Content-Type": "application/json"},
        )
        logger.info("[LLMFactory] 创建 ChatOpenAI (MiniMax): model=%s", config.pure_model)

    else:
        # 默认 OpenAI 兼容接口
        llm = ChatOpenAI(
            model=config.pure_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key=config.api_key,
            base_url=config.base_url,
        )
        logger.info("[LLMFactory] 创建 ChatOpenAI (默认): model=%s", config.pure_model)

    return llm, config


def create_llm(
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> Tuple[object, LLMConfig]:
    """
    一站式工厂方法: 解析配置 + 创建 LLM 实例。

    Args:
        model_name:  模型名称, 默认从环境变量获取
        base_url:    API 地址, 默认从环境变量获取
        api_key:     API Key, 默认从环境变量获取
        temperature: 温度参数
        max_tokens:  最大 token 数

    Returns:
        (llm_instance, config) 元组

    Raises:
        ValueError: 缺少必要的配置
    """
    config = resolve_model_config(
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return build_llm_instance(config)


def create_llm_from_env(
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> Tuple[object, LLMConfig]:
    """
    从环境变量创建 LLM 实例 (最常用入口)。

    读取的环境变量:
      - AI_API_KEY
      - AI_BASE_URL
      - AI_MODEL_NAME (可选, 默认 doubao-seed-2-0-pro-260215)

    Returns:
        (llm_instance, config) 元组
    """
    return create_llm(temperature=temperature, max_tokens=max_tokens)
