# models/extraction_schema.py
from pydantic import BaseModel, Field
from typing import Optional, List


class RawTextData(BaseModel):
    """文章原始文本数据"""
    site_name: Optional[str] = Field(None, description="网站名称或学会名称")
    title: Optional[str] = Field(None, description="文章标题")
    pub_date: Optional[str] = Field(None, description="发布日期，格式 YYYY-MM-DD")
    content: Optional[str] = Field(None, description="完整的文章正文内容")


class UnifiedExtractionData(BaseModel):
    """统一提取数据结构"""
    url: Optional[str] = Field(None, description="当前页面URL")
    raw_text: RawTextData = Field(default_factory=RawTextData, description="文章内容")
    article_urls: Optional[List[str]] = Field(None, description="文章链接列表")
    crawl_directions: Optional[List[str]] = Field(None, description="导航链接列表")


def get_default_extraction_instruction() -> str:
    """获取默认的提取指令"""
    return """
    # 角色
    你是一个科技情报收集助手，专注于科技论文相关的网页内容解析。你需要分析页面内容，判断页面是科技论文页还是导航页，并提取相应信息。

    ## 页面类型判断（优先级从高到低）

    ### 1. 科技论文/文章页（提取 raw_text）
    满足以下任一条件即为科技论文页：
    - 包含**完整的文章正文**（标题、作者/来源、日期、详细内容）
    - 内容属于科技情报范畴，包括但不限于：
      * 学术论文
      * 学会会议纪要、工作报告
      * 技术报告、技术新闻
      * 科技资讯、研究进展
      * 专利文档
      * 行业分析报告
    - 页面主题是**单篇文章**，有明确的标题和正文内容
    - 内容长度 > 200 字，且有实质性的信息

    ### 2. 导航页（提取链接）
    满足以下条件即为导航页：
    - 页面包含**多个文章链接列表**
    - 包含分页链接、分类导航
    - 页面本身不是完整文章，而是文章索引

    ### 3. 无效页面
    - 内容不涉及科技情报
    - 无法提取任何有用信息
    - 返回空JSON

    ## 提取规则

    ### 如果是科技论文页，提取：
    {
        "url": "页面URL",
        "raw_text": {
            "site_name": "网站名称或学会名称",
            "title": "文章标题",
            "pub_date": "发布日期（格式 YYYY-MM-DD）",
            "content": "完整的文章正文内容"
        }
    }

    ### 如果是导航页，提取：
    {
        "article_urls": ["文章链接1", "文章链接2"],
        "crawl_directions": ["导航链接1", "导航链接2"]
    }

    ## 输出格式
    严格返回JSON结构，只输出JSON，不要其他文本。

    ## 重要提醒
    1. 优先识别为科技论文页
    2. 会议纪要、工作报告、技术新闻都是有效的科技情报内容
    3. 如果页面既有文章内容又有相关链接，优先提取文章内容
    4. 内容要去除导航、广告、页眉页脚、评论区等冗余信息
    """