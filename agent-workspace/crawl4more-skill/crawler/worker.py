# crawler/worker.py
import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Optional

from models.job import CrawlJob
from models.subtask import CrawlSubtask
from services.task_manager import TaskManager

logger = logging.getLogger(__name__)

# crawl4ai 为重量级依赖（含浏览器），延迟导入：
# mock 模式（USE_MOCK_CRAWLER=true 或 LLM 不可用）不依赖 crawl4ai / langchain / pydantic，
# 只有进入真实爬取模式时才要求这些包可用。
_CRAWL4AI_IMPORT_ERROR = None
try:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode, BrowserConfig
    from crawl4ai import LLMConfig as Crawl4AILLMConfig, LLMExtractionStrategy
    CRAWL4AI_AVAILABLE = True
except ImportError as e:  # pragma: no cover - 取决于运行环境
    AsyncWebCrawler = CrawlerRunConfig = CacheMode = BrowserConfig = None
    Crawl4AILLMConfig = LLMExtractionStrategy = None
    CRAWL4AI_AVAILABLE = False
    _CRAWL4AI_IMPORT_ERROR = e


class CrawlerWorker:
    """爬虫 Worker - 集成 Crawl4AI 真实爬虫"""

    def __init__(self, task_manager: TaskManager, job: CrawlJob):
        self.task_manager = task_manager
        self.job = job
        self.running = False
        self.processed_urls = set()

        # ========== 使用 LLMFactory 创建 LLM（langchain 延迟导入，mock 模式不依赖） ==========
        self.llm_available = False
        if not CRAWL4AI_AVAILABLE:
            logger.warning(f"[Worker] crawl4ai 未安装 ({_CRAWL4AI_IMPORT_ERROR})，仅可使用模拟模式")
        else:
            try:
                from utils.llm_factory import create_llm_from_env
                self.llm, self.llm_config = create_llm_from_env()
                logger.info(f"[Worker] LLM 初始化成功: {self.llm_config.fixed_model}")
                self.llm_available = True
            except Exception as e:
                logger.warning(f"[Worker] LLM 初始化失败: {e}，使用模拟模式")

        # ========== 浏览器配置（优化资源占用，仅真实模式需要） ==========
        self.browser_config = None
        if CRAWL4AI_AVAILABLE:
            self.browser_config = BrowserConfig(
                headless=os.getenv("CRAWLER_HEADLESS", "true").lower() == "true",
                viewport_width=int(os.getenv("CRAWLER_VIEWPORT_WIDTH", "1024")),
                viewport_height=int(os.getenv("CRAWLER_VIEWPORT_HEIGHT", "768")),
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                text_mode=os.getenv("CRAWLER_TEXT_MODE", "true").lower() == "true",
                verbose=os.getenv("CRAWLER_VERBOSE", "false").lower() == "true",
                browser_type="chromium",
                extra_args=[
                    # 禁用 GPU 加速（你的电脑不支持）
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-accelerated-2d-canvas",
                    "--disable-accelerated-jpeg-decoding",
                    "--disable-accelerated-mjpeg-decode",
                    "--disable-accelerated-video-decode",
                    "--disable-gpu-sandbox",
                    "--disable-gpu-compositing",
                    "--disable-gpu-rasterization",
                    "--disable-gpu-process-crash-limit",
                    # 性能优化
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-setuid-sandbox",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--use-gl=swiftshader",  # 软件渲染替代 GPU
                    # 资源优化
                    "--disable-extensions",
                    "--disable-plugins",
                    "--disable-images",  # 不加载图片
                    "--blink-settings=imagesEnabled=false",
                ]
            )

        # ========== 提取策略 ==========
        self.extraction_strategy = None
        self.crawler = None

        # 如果 LLM 可用，设置提取策略
        if self.llm_available:
            self._setup_extraction_strategy()

    def _setup_extraction_strategy(self):
        """设置提取策略（pydantic schema 延迟导入，mock 模式不依赖）"""
        try:
            from models.extraction_schema import (
                UnifiedExtractionData,
                get_default_extraction_instruction,
            )

            # 创建 Crawl4AI 的 LLM 配置
            crawl4ai_llm = Crawl4AILLMConfig(
                provider=self.llm_config.fixed_model,
                api_token=self.llm_config.api_key,
                base_url=self.llm_config.base_url,
                max_tokens=8192,  # 增加输出 token 限制
            )

            self.extraction_strategy = LLMExtractionStrategy(
                llm_config=crawl4ai_llm,
                schema=UnifiedExtractionData.model_json_schema(),
                extraction_type="schema",
                instruction=get_default_extraction_instruction(),
                chunk_token_threshold=1024,  # 减小 chunk 大小，避免单块过大
                apply_chunking=True,
                input_format="markdown",
                max_tokens=8192,  # 与 LLM 配置保持一致
                verbose=True,  # 开启详细日志，方便调试
                word_count_threshold=10,  # 忽略过短的文本块
            )
            logger.info("[Worker] ✅ 提取策略设置完成 (max_tokens=8192, chunk_size=1024)")
        except Exception as e:
            logger.warning(f"[Worker] 提取策略设置失败: {e}")
            self.llm_available = False

    async def run(self):
        """运行 Worker"""
        self.running = True
        logger.info(f"[Worker] 🚀 开始处理作业: {self.job.job_uuid}")

        # 检查是否启用模拟模式
        use_mock = os.getenv("USE_MOCK_CRAWLER", "false").lower() == "true"

        if use_mock or not self.llm_available:
            logger.info(f"[Worker] 使用模拟模式 (LLM可用: {self.llm_available})")
            await self._run_mock_mode()
            return

        # 真实爬虫模式
        await self._run_real_mode()
        logger.info(f"[Worker] 作业处理结束: {self.job.job_uuid}")

    async def _run_real_mode(self):
        """真实爬虫模式"""
        logger.info(f"[Worker] 进入真实爬虫模式")

        try:
            # 启动浏览器（带超时）
            logger.info(f"[Worker] 正在启动浏览器...")
            async with asyncio.timeout(60):
                self.crawler = AsyncWebCrawler(config=self.browser_config)
                await self.crawler.__aenter__()
                logger.info(f"[Worker] ✅ 浏览器启动成功")

            # 处理所有子任务
            while self.running:
                # 1. 检查作业状态
                job = self.task_manager.get_job(self.job.id)
                if not job or job.status in ('completed', 'failed', 'cancelled'):
                    break

                if job.status == 'paused':
                    logger.debug(f"[Worker] 作业已暂停，等待恢复")
                    await asyncio.sleep(2)
                    continue

                if job.status not in ('running', 'pending'):
                    break

                # 2. 检查最大页面数
                if job.processed_pages >= job.max_pages:
                    logger.info(f"[Worker] 达到最大页面数 {job.max_pages}，作业完成")
                    self.task_manager.update_job_status(self.job.id, 'completed')
                    break

                # 3. 获取下一个子任务
                subtask = self.task_manager.get_next_pending_subtask(self.job.id)
                if not subtask:
                    logger.info(f"[Worker] 没有更多子任务，作业完成")
                    self.task_manager.update_job_status(self.job.id, 'completed')
                    break

                # 4. 检查时间片
                if self.task_manager.is_time_slice_expired(self.job.id):
                    logger.info(f"[Worker] 时间片到期，暂停作业")
                    self.task_manager.update_job_status(self.job.id, 'paused')
                    break

                # 5. 爬取
                await self._crawl_real(subtask)

                # 6. 爬取完成后延迟，减轻服务器压力
                await asyncio.sleep(1)

        except asyncio.TimeoutError:
            logger.error(f"[Worker] 浏览器启动超时 (60秒)")
            self.task_manager.update_job_status(self.job.id, 'failed', '浏览器启动超时')
        except Exception as e:
            logger.error(f"[Worker] 浏览器启动失败: {e}")
            self.task_manager.update_job_status(self.job.id, 'failed', str(e))
        finally:
            if self.crawler:
                try:
                    await self.crawler.__aexit__(None, None, None)
                    logger.info(f"[Worker] 浏览器已关闭")
                except Exception as e:
                    logger.warning(f"[Worker] 关闭浏览器时出错: {e}")

    async def _crawl_real(self, subtask: CrawlSubtask):
        """使用 Crawl4AI 爬取单个 URL（带重试）"""
        self.task_manager.update_subtask_status(subtask.id, 'running')
        logger.info(f"[Worker] 爬取 URL: {subtask.url}")

        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                run_config = CrawlerRunConfig(
                    page_timeout=90000,
                    cache_mode=CacheMode.DISABLED,
                    extraction_strategy=self.extraction_strategy,
                    word_count_threshold=1,
                    wait_until="domcontentloaded",
                    delay_before_return_html=2.0,
                )

                result = await self.crawler.arun(subtask.url, config=run_config)

                if not result or not result.extracted_content:
                    raise Exception("爬取结果为空")

                if "LLM returned no content" in result.extracted_content:
                    raise Exception(f"LLM 返回空内容")

                try:
                    data_list = json.loads(result.extracted_content)
                    if not data_list:
                        raise Exception("提取结果为空列表")
                    data = data_list[0]
                except json.JSONDecodeError as e:
                    logger.error(f"[Worker] JSON解析失败: {e}")
                    logger.error(f"[Worker] 原始内容: {result.extracted_content[:500]}")
                    raise Exception(f"JSON解析失败: {e}")

                # 更新子任务为完成
                self.task_manager.update_subtask_status(
                    subtask.id, 'completed',
                    extracted_data=data
                )

                processed = self.task_manager.increment_processed_pages(self.job.id)
                logger.info(f"[Worker] 已处理 {processed}/{self.job.max_pages} 页")

                # ✅ 安全调用 _discover_new_urls
                if data:
                    await self._discover_new_urls(data)
                else:
                    logger.warning(f"[Worker] data 为空，跳过 URL 发现")

                return

            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(f"[Worker] 超时 (尝试 {attempt + 1}/{max_retries + 1}): {subtask.url}")
                if attempt < max_retries:
                    await asyncio.sleep(3)

            except Exception as e:
                last_error = e
                logger.warning(f"[Worker] 失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(3)

        logger.error(f"[Worker] 爬取失败: {subtask.url} - {last_error}")
        self.task_manager.update_subtask_status(
            subtask.id, 'failed', error_message=str(last_error)
        )

    async def _discover_new_urls(self, data: dict):
        """发现新 URL 并添加到队列（安全版本）"""
        # ✅ 安全获取，默认空列表
        article_urls = data.get('article_urls')
        crawl_dirs = data.get('crawl_directions')

        if article_urls is None:
            article_urls = []
        if crawl_dirs is None:
            crawl_dirs = []

        # 确保是列表类型
        if not isinstance(article_urls, list):
            logger.warning(f"[Worker] article_urls 不是列表类型: {type(article_urls)}")
            article_urls = []
        if not isinstance(crawl_dirs, list):
            logger.warning(f"[Worker] crawl_directions 不是列表类型: {type(crawl_dirs)}")
            crawl_dirs = []

        # 过滤无效 URL
        def is_valid_url(url):
            if not url:
                return False
            if not isinstance(url, str):
                return False
            if url.startswith('javascript:'):
                return False
            if url.strip() == '':
                return False
            return True

        article_urls = [u for u in article_urls if is_valid_url(u)]
        crawl_dirs = [u for u in crawl_dirs if is_valid_url(u)]

        added = 0
        if article_urls:
            count = self.task_manager.add_subtasks(
                self.job.id, article_urls, 'article'
            )
            added += count
            logger.info(f"[Worker] 发现 {count} 个新文章链接")

        if crawl_dirs:
            count = self.task_manager.add_subtasks(
                self.job.id, crawl_dirs, 'nav'
            )
            added += count
            logger.info(f"[Worker] 发现 {count} 个新导航链接")

        return added

    async def _run_mock_mode(self):
        """模拟模式（当 LLM 配置不可用时）"""
        import random
        logger.info(f"[Worker] 进入模拟模式")

        while self.running:
            job = self.task_manager.get_job(self.job.id)
            if not job or job.status in ('completed', 'failed', 'cancelled'):
                break

            if job.status == 'paused':
                await asyncio.sleep(2)
                continue

            if job.status not in ('running', 'pending'):
                break

            if job.processed_pages >= job.max_pages:
                logger.info(f"[Worker] 达到最大页面数 {job.max_pages}，作业完成")
                self.task_manager.update_job_status(self.job.id, 'completed')
                break

            subtask = self.task_manager.get_next_pending_subtask(self.job.id)
            if not subtask:
                logger.info(f"[Worker] 没有更多子任务，作业完成")
                self.task_manager.update_job_status(self.job.id, 'completed')
                break

            if self.task_manager.is_time_slice_expired(self.job.id):
                logger.info(f"[Worker] 时间片到期，暂停作业")
                self.task_manager.update_job_status(self.job.id, 'paused')
                break

            # 模拟爬取
            self.task_manager.update_subtask_status(subtask.id, 'running')
            await asyncio.sleep(1.5)

            self.task_manager.update_subtask_status(
                subtask.id, 'completed',
                extracted_data={
                    'url': subtask.url,
                    'raw_text': {
                        'title': f'模拟文章: {subtask.url[:50]}',
                        'content': '这是模拟的文章内容...',
                        'site_name': '模拟站点',
                        'pub_date': '2026-01-01'
                    }
                }
            )
            self.task_manager.increment_processed_pages(self.job.id)

            # 模拟发现新 URL
            if random.random() < 0.3:
                mock_urls = [
                    f"{subtask.url}/article/{i}" for i in range(1, 4)
                ]
                self.task_manager.add_subtasks(self.job.id, mock_urls, 'article')
                logger.info(f"[Worker] 模拟发现 {len(mock_urls)} 个新 URL")

        logger.info(f"[Worker] 模拟模式结束")