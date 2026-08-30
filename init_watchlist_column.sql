-- ============================================================
-- Chilam Club · 为 users 表补 watchlist 列
-- 在 Supabase Dashboard > SQL Editor 执行
-- ============================================================
--
-- 为什么必须先做这一步：
-- `database.update_user_watchlist()` 用 PATCH users.watchlist 保存自选股，
-- 但 users 表**从来没有这一列**。实测 PostgREST 返回：
--   400 PGRST204 Could not find the 'watchlist' column of 'users'
-- 而 `_supabase_request()` 捕获 HTTPError 后 return None，调用方只得到 False，
-- 页面还会显示「已在本地更新自选清单」——于是自选股只活在 st.session_state 里，
-- 用户一刷新就没了，跨设备也看不到。
--
-- 这直接决定了「三段付费闭环」的第一段能否成立：个性化摘要要靠自选股算，
-- 自选股存不住，付费用户收到的邮件就跟免费内容一模一样，付费理由随之消失。
-- ============================================================

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS watchlist JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.users.watchlist IS
    '用户自选股代码数组，如 ["000001","600519"]。个性化摘要的唯一数据来源。';

-- 邮件投递开关：用户可以是付费会员但不想收邮件（避免变成骚扰）
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS digest_optin BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN public.users.digest_optin IS
    '是否接收每日收盘摘要邮件。退订只需置 FALSE，不影响其他会员权益。';

-- 核验：应返回 watchlist / digest_optin 两行
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
  AND column_name IN ('watchlist', 'digest_optin');
