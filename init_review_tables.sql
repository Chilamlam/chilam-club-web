-- ============================================================
-- Chilam Club 引导式复盘答卷 - 数据表
-- 在 Supabase Dashboard > SQL Editor 执行此文件
--
-- 三表结构：
--   review_answers    用户答卷（每用户每日一份，交卷后锁定）
--   review_ai_answers AI 答卷（每日一份，全站共用，带数据依据）
--   review_scoring    次日回验（用户 vs AI vs 实际盘面，逐题命中）
--
-- 设计要点：
--   1. AI 答卷与用户答卷分表 —— 解锁门禁在后端控制：用户交卷前，
--      查询 AI 答卷的 API 直接拒绝，而不是前端藏起来。
--   2. answers 用 JSONB 存逐题作答 {题目id: 选项值}，模板演进
--      （增删题）不破坏旧答卷 —— scoring 按题目 id 对账。
--   3. RLS：用户只能读写自己的答卷与回验；AI 答卷对所有登录
--      用户只读（且应用层再查一次交卷状态）。
-- ============================================================

-- ---------- 1. 用户答卷 ----------
CREATE TABLE IF NOT EXISTS public.review_answers (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    trade_date   DATE NOT NULL,                -- 盘面所属交易日（非填卷日）
    answers      JSONB NOT NULL DEFAULT '{}', -- {题目id: 作答值}
    plan_text    TEXT,                         -- 明日操作计划（自由文本，不判分）
    submitted    BOOLEAN NOT NULL DEFAULT FALSE, -- 交卷后锁定，不可改
    submitted_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, trade_date)               -- 每人每日一份
);

CREATE INDEX IF NOT EXISTS idx_review_answers_date ON public.review_answers(trade_date);
CREATE INDEX IF NOT EXISTS idx_review_answers_user ON public.review_answers(user_id);

-- ---------- 2. AI 答卷（每日一份全站共用） ----------
CREATE TABLE IF NOT EXISTS public.review_ai_answers (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trade_date   DATE NOT NULL UNIQUE,         -- 每交易日一份
    answers      JSONB NOT NULL DEFAULT '{}', -- {题目id: {choice: 值, basis: 数据依据}}
    plan_text    TEXT,                         -- AI 明日预期（结构描述口径）
    data_digest  TEXT,                         -- AI 作答时的数据快照摘要（可追溯）
    model_name   VARCHAR(60),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 3. 次日回验 ----------
CREATE TABLE IF NOT EXISTS public.review_scoring (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trade_date      DATE NOT NULL,             -- 被回验答卷的交易日
    user_id         BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    question_id     VARCHAR(40) NOT NULL,      -- 模板题目 id
    user_choice     TEXT,                       -- 用户当日作答
    ai_choice       TEXT,                       -- AI 当日作答
    actual          TEXT,                       -- 实际盘面判定（次日数据回验）
    user_hit        BOOLEAN,                    -- 用户是否命中
    ai_hit          BOOLEAN,                     -- AI 是否命中
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, trade_date, question_id)
);

CREATE INDEX IF NOT EXISTS idx_review_scoring_user ON public.review_scoring(user_id, trade_date);

-- ---------- 行级安全 ----------
ALTER TABLE public.review_answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_ai_answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_scoring ENABLE ROW LEVEL SECURITY;

-- 用户对自己答卷：读写
DROP POLICY IF EXISTS "own review answers rw" ON public.review_answers;
CREATE POLICY "own review answers rw" ON public.review_answers
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
-- 注：应用走 service key（auth.uid() 为空），此策略面向未来直连场景；
--     当前所有读写经应用层（database.py），应用层再校验 user_id。

-- AI 答卷：应用层写入（service key 绕过 RLS），此处仅防御性只读策略
DROP POLICY IF EXISTS "ai answers read all" ON public.review_ai_answers;
CREATE POLICY "ai answers read all" ON public.review_ai_answers
    FOR SELECT USING (true);

-- 回验：用户只读自己的
DROP POLICY IF EXISTS "own scoring read" ON public.review_scoring;
CREATE POLICY "own scoring read" ON public.review_scoring
    FOR SELECT USING (auth.uid() = user_id);
