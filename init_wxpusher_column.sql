-- 微信推送绑定所需列（在 Supabase → SQL Editor 里执行一次即可）
--
-- 为什么单独一个文件而不是塞进 init_watchlist_column.sql：
-- 那个文件你已经执行过了，再改它会让「已执行/未执行」变得说不清。
-- 迁移脚本一旦跑过就当作不可变，新需求追加新文件。
--
-- 两列都用 IF NOT EXISTS，重复执行安全。

ALTER TABLE users ADD COLUMN IF NOT EXISTS wxpusher_uid TEXT;

-- 推送总开关：默认 TRUE（付费即默认接收），用户可在站内关掉。
-- 与 digest_optin 的区别：digest_optin 管「要不要收摘要」这件事本身，
-- 这里不再加第二个开关，避免两个开关语义重叠导致「关了还收到」的投诉。

-- 便于按 UID 反查用户（解绑、去重）
CREATE INDEX IF NOT EXISTS idx_users_wxpusher_uid ON users (wxpusher_uid);

-- 同一个微信不允许绑定到多个账号：否则一个人会收到多份不同的个性化摘要，
-- 且解绑时不知道该清哪一条。部分唯一索引跳过 NULL（未绑定的用户不受限）。
CREATE UNIQUE INDEX IF NOT EXISTS uniq_users_wxpusher_uid
    ON users (wxpusher_uid) WHERE wxpusher_uid IS NOT NULL;

-- 执行后自查（应能看到 wxpusher_uid 列）：
-- SELECT column_name, data_type FROM information_schema.columns
--  WHERE table_name = 'users' ORDER BY ordinal_position;
