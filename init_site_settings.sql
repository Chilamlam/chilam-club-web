-- ============================================================
-- Chilam Club 站点运营设置 - site_settings 表
-- 在 Supabase Dashboard > SQL Editor 执行此文件（幂等，可重复执行）
-- ============================================================
--
-- 为什么需要这张表
-- ----------------
-- 「侧边栏店铺入口要不要显示」这类**运营侧一次性决定**，如果写成代码常量，
-- 每次切换都要改代码 + 提交 + 等 Cloud 重新部署（几分钟到十几分钟），
-- 关店/开店这种「今天就要生效」的事等不起。
-- 存进表里之后，管理员在后台页点一下即可，对所有访客立即生效，
-- 且不需要会改代码。
--
-- 安全设计
-- --------
-- 这张表只给后端（service_role / service_key）读写，**不给 anon 与
-- authenticated 任何策略**。开了 RLS 又不建策略 = 默认全拒，
-- 所以前端即使拿到 anon key 也读不到、更写不到。
-- 页面里的读写一律走服务端 service_key（见 database.py）。
-- ============================================================

CREATE TABLE IF NOT EXISTS public.site_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT
);

-- 行级安全：开启但不建任何策略 → anon / authenticated 一律拒绝
ALTER TABLE public.site_settings ENABLE ROW LEVEL SECURITY;

-- 只授给 service_role（PostgREST 带 service key 时自动绕过 RLS）
GRANT ALL ON public.site_settings TO service_role;

COMMENT ON TABLE  public.site_settings        IS '站点运营开关（key/value），仅服务端 service_role 读写';
COMMENT ON COLUMN public.site_settings.key    IS '开关名，例如 shop_entry_enabled';
COMMENT ON COLUMN public.site_settings.value  IS '取值，勾选用字符串 ''1''，关闭用 ''0''';

-- ============================================================
-- 初始数据：店铺已关，入口隐藏
--   用 ON CONFLICT DO NOTHING：**重复执行本文件不会把管理员后来
--   打开的状态覆盖回 ''0''**。这条很重要——迁移脚本必须幂等且不
--   破坏线上已有配置，否则「补跑一次建表脚本」会静默把店铺又关掉。
-- ============================================================
INSERT INTO public.site_settings (key, value, updated_by)
VALUES ('shop_entry_enabled', '0', 'init_site_settings.sql')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
-- 验证（执行后可选跑一下，应看到 1 行）
-- ============================================================
-- SELECT key, value, updated_at FROM public.site_settings;
