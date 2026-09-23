-- ============================================================
-- Chilam Club 密码重置 - 数据表
-- 在 Supabase Dashboard > SQL Editor 执行此文件（一次即可，重复执行安全）
--
-- 两张表：
--   password_reset_codes     重置码（只存盐与哈希，绝不存明文）
--   password_reset_requests  人工重置申请（自助通道不可用时的兜底待办）
--
-- 设计要点：
--   1. **明文码不落库**。库里是 code_salt + code_hash，明文只出现在发给
--      用户的那条消息里。任何拿到数据库的人都不能直接重置某个账号。
--   2. **单次使用由 consumed_at 表达**（NULL = 未用）。发新码前会把该用户
--      所有未消费码一并作废（应用层调用 invalidate_reset_codes），
--      避免同账号并存多枚有效码把「一次性」稀释掉。
--   3. **attempts 用来封顶试错**。只有有效期没有次数上限，等于给暴力猜
--      留了 15 分钟窗口；两个一起才成立。
--   4. RLS：**启用且不给任何策略**（见文件末尾说明）。
-- ============================================================

-- ---------- 1. 重置码 ----------
CREATE TABLE IF NOT EXISTS public.password_reset_codes (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    code_salt   TEXT NOT NULL,               -- 随机盐（hex）
    code_hash   TEXT NOT NULL,               -- sha256(salt:归一化后的码)
    channel     VARCHAR(20) NOT NULL,        -- wxpusher / email / admin
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,                 -- 非 NULL 即已用/已作废
    attempts    INTEGER NOT NULL DEFAULT 0,  -- 已试错次数
    delivered   BOOLEAN NOT NULL DEFAULT FALSE,
    note        TEXT,                        -- 投递结果说明（**不含码本身**）
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 限流与「取最近一条」都按 (user_id, created_at desc) 查
CREATE INDEX IF NOT EXISTS idx_reset_codes_user_time
    ON public.password_reset_codes(user_id, created_at DESC);

-- 清理用：按到期时间扫（过期码可定期删除，留着也无害）
CREATE INDEX IF NOT EXISTS idx_reset_codes_expires
    ON public.password_reset_codes(expires_at);

-- ---------- 2. 人工重置申请 ----------
CREATE TABLE IF NOT EXISTS public.password_reset_requests (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    note       TEXT,
    handled_at TIMESTAMPTZ,                  -- 站长处理完打上时间戳
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reset_requests_pending
    ON public.password_reset_requests(handled_at, created_at DESC);

-- ============================================================
-- 行级安全：启用，且**不创建任何策略**
-- ------------------------------------------------------------
-- 与 payments / review_* 表不同——那几张表挂的是
-- `auth.role() = 'authenticated'` 的 SELECT 策略。这里刻意一条都不给：
--
--   · 本站**不使用 Supabase Auth**（登录态是自签 JWT，见 auth.py），
--     PostgREST 永远不会以 authenticated 角色出现；
--   · 应用侧用的是 `sb_secret_*` 密钥（等价 service_role），**绕过 RLS**。
--     这一点是实测确认的：用 secrets.toml 里的 key / service_key 分别
--     直连 /rest/v1/payments 均返回 200——而 payments 只挂了
--     authenticated 策略、当前又无 Auth 会话，能读到就说明走的是
--     service_role 路径。
--
-- 结论：不给策略 = 任何非 service_role 的访问（anon / 经 API 直连的第三方）
-- 一律读不到**任何**重置码行，而应用功能完全不受影响。
-- 重置码是凭据类数据，宁可「除服务端外谁都读不到」，也不要为了一致性
-- 挂一条「反正在当前架构下也不会命中」的策略。
-- ============================================================
ALTER TABLE public.password_reset_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.password_reset_requests ENABLE ROW LEVEL SECURITY;

-- 执行后自查（应看到两张表、且 rowsecurity 均为 true）：
-- SELECT relname, relrowsecurity FROM pg_class
--  WHERE relname IN ('password_reset_codes','password_reset_requests');
