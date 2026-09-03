-- 目录：waku-agent/sql/init_supabase.sql
-- 原样取自 launch-agentic-rag（github.com/ShenSeanChen/launch-agentic-rag），
-- 使向量检索升级路径能够复用该系列视频所讲解的准确架构。
--
-- RAG 应用的完整 Supabase 数据库配置
-- 请在全新的 Supabase 项目中执行此脚本
-- 
-- 操作步骤：
-- 1. 在 https://supabase.com 创建一个新的 Supabase 项目
-- 2. 等待项目初始化完成
-- 3. 进入 Supabase 控制台中的 SQL Editor
-- 4. 新建一个查询
-- 5. 复制并粘贴*完整*脚本
-- 6. 点击“Run”一次性执行全部内容
--
-- 此脚本将从零开始创建所需内容

-- =============================================================================
-- 第 1 步：启用用于向量相似度搜索的 pgvector 扩展
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- 第 2 步：创建主 RAG 分块表
-- =============================================================================

CREATE TABLE rag_chunks (
    id BIGSERIAL PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(1536),  -- OpenAI text-embedding-3-small 的维度（1536）
    created_at TIMESTAMPTZ DEFAULT now()
);

-- =============================================================================
-- 第 3 步：创建用于快速向量搜索的性能索引
-- =============================================================================

-- 使用 IVFFlat 算法的向量相似度索引（适用于 1536 维）
-- 为兼容性使用 text-embedding-3-small（1536 维）
CREATE INDEX rag_chunks_vec_idx
    ON rag_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 用于筛选和排序的普通 B-tree 索引
CREATE INDEX rag_chunks_src_idx ON rag_chunks (source);
CREATE INDEX rag_chunks_chunk_id_idx ON rag_chunks (chunk_id);
CREATE INDEX rag_chunks_created_at_idx ON rag_chunks (created_at DESC);

-- =============================================================================
-- 第 4 步：创建向量相似度搜索函数
-- =============================================================================

CREATE OR REPLACE FUNCTION match_chunks (
  query_embedding vector(1536),
  match_count int DEFAULT 6,
  min_similarity float DEFAULT 0.0
)
RETURNS TABLE (
  chunk_id text,
  source text,
  text text,
  similarity float,
  created_at timestamptz
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    rag_chunks.chunk_id,
    rag_chunks.source,
    rag_chunks.text,
    1 - (rag_chunks.embedding <=> query_embedding) as similarity,
    rag_chunks.created_at
  FROM rag_chunks
  WHERE 1 - (rag_chunks.embedding <=> query_embedding) >= min_similarity
  ORDER BY rag_chunks.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- =============================================================================
-- 第 5 步：创建获取数据库统计信息的辅助函数
-- =============================================================================

CREATE OR REPLACE FUNCTION get_chunk_stats()
RETURNS TABLE (
  total_chunks bigint,
  unique_sources bigint,
  latest_chunk timestamptz
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    COUNT(*) as total_chunks,
    COUNT(DISTINCT source) as unique_sources,
    MAX(created_at) as latest_chunk
  FROM rag_chunks;
END;
$$;

-- =============================================================================
-- 第 6 步：创建行级安全性（RLS）策略
-- =============================================================================

-- 为该表启用 RLS
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;

-- 允许服务角色（后端）执行全部操作
CREATE POLICY "Allow service role full access" ON rag_chunks
  FOR ALL USING (auth.jwt() ->> 'role' = 'service_role');

-- 允许已认证用户读取（供未来前端使用）
CREATE POLICY "Allow authenticated read access" ON rag_chunks
  FOR SELECT USING (auth.role() = 'authenticated');

-- 开发环境允许匿名读取（生产环境应删除）
CREATE POLICY "Allow anonymous read access" ON rag_chunks
  FOR SELECT USING (true);

-- =============================================================================
-- 第 7 步：验证——检查是否已正确创建全部内容
-- =============================================================================

-- 测试 1：检查 pgvector 扩展
SELECT 'pgvector extension installed' as test_result 
WHERE EXISTS (
  SELECT 1 FROM pg_extension WHERE extname = 'vector'
);

-- 测试 2：检查表是否创建
SELECT 'rag_chunks table created' as test_result 
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables 
  WHERE table_schema = 'public' AND table_name = 'rag_chunks'
);

-- 测试 3：检查向量列维度
SELECT 
  'Vector column configured for text-embedding-3-small' as test_result,
  'VECTOR(1536) dimensions' as details
WHERE EXISTS (
  SELECT 1 FROM information_schema.columns 
  WHERE table_name = 'rag_chunks' 
  AND column_name = 'embedding'
);

-- 测试 4：检查函数
SELECT 'match_chunks function created' as test_result 
WHERE EXISTS (
  SELECT 1 FROM information_schema.routines 
  WHERE routine_schema = 'public' AND routine_name = 'match_chunks'
);

SELECT 'get_chunk_stats function created' as test_result 
WHERE EXISTS (
  SELECT 1 FROM information_schema.routines 
  WHERE routine_schema = 'public' AND routine_name = 'get_chunk_stats'
);

-- 测试 5：检查索引
SELECT 'Vector index created' as test_result
WHERE EXISTS (
  SELECT 1 FROM pg_indexes 
  WHERE tablename = 'rag_chunks' AND indexname = 'rag_chunks_vec_idx'
);

-- 测试 6：显示初始数据库统计信息（应为空）
SELECT 
  'Database ready - ' || total_chunks::text || ' chunks' as test_result
FROM get_chunk_stats();

-- =============================================================================
-- 成功提示
-- =============================================================================

SELECT '🎉 SUCCESS! Your Supabase database is ready for RAG!' as final_result;

-- =============================================================================
-- 已创建的内容：
-- =============================================================================
-- 
-- ✅ 扩展：
--    - pgvector（用于向量操作）
-- 
-- ✅ 表：
--    - rag_chunks（使用适配 text-embedding-3-small 的 VECTOR(1536)）
-- 
-- ✅ 索引：
--    - IVFFlat 向量索引（针对 1536 维优化）
--    - 用于快速筛选的 B-tree 索引
-- 
-- ✅ 函数：
--    - match_chunks() - 向量相似度搜索
--    - get_chunk_stats() - 数据库统计信息
-- 
-- ✅ 安全性：
--    - 已启用行级安全性
--    - 服务角色、已认证用户和匿名访问的策略
-- 
-- =============================================================================
-- 后续步骤：
-- =============================================================================
-- 
-- 1. 在 .env 文件中填写 Supabase 凭据：
--    SUPABASE_URL=https://your-project.supabase.co
--    SUPABASE_ANON_KEY=your_anon_key
--    SUPABASE_SERVICE_ROLE_KEY=your_service_key
--    OPENAI_API_KEY=your_openai_key
-- 
-- 2. 启动 FastAPI 后端：
--    uvicorn main:app --reload --port 8000
-- 
-- 3. 测试健康检查：
--    curl http://localhost:8000/healthz
-- 
-- 4. 写入知识库种子数据：
--    curl -X POST http://localhost:8000/seed
-- 
-- 5. 提出第一个问题：
--    curl -X POST http://localhost:8000/answer \
--      -H "Content-Type: application/json" \
--      -d '{"query": "What is your return policy?"}'
-- 
-- 6. 访问交互式文档：
--    http://localhost:8000/docs
-- 
-- =============================================================================
