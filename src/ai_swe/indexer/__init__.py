"""
Repository indexing and understanding subsystem.

Public surface:

    from ai_swe.indexer import CodebaseAnalyzer
    from ai_swe.indexer.models import RepositoryIndex

Example::

    import asyncio
    from pathlib import Path
    from ai_swe.indexer import CodebaseAnalyzer

    index = asyncio.run(CodebaseAnalyzer(".").analyze_repository())
    print(index.statistics)
"""

from ai_swe.indexer.analyzer import CodebaseAnalyzer

__all__ = ["CodebaseAnalyzer"]
