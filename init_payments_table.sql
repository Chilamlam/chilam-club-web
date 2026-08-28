-- ============================================================
-- Chilam Club 付费系统 - payments 订单表
-- 在 Supabase Dashboard > SQL Editor 执行此文件
-- ============================================================

CREATE TABLE IF NOT EXISTS public.payments (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    order_no        VARCHAR(40) NOT NULL UNIQUE,
    plan_name       VARCHAR(20) NOT NULL,
    months          INT NOT NULL DEFAULT 1,
    amount          NUMERIC(10,2) NOT NULL,
    currency        VARCHAR(10) NOT NULL DEFAULT 'CNY',
    payment_method  VARCHAR(20) NOT NULL DEFAULT 'wechat',
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at    TIMESTAMPTZ,
    confirmed_by    BIGINT,
    note            TEXT
);

-- 索引：按状态查询待处理订单
CREATE INDEX IF NOT EXISTS idx_payments_status ON public.payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_user   ON public.payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_order  ON public.payments(order_no);

-- 行级安全 (RLS)
ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;

-- 用户只能查看自己的订单
CREATE POLICY payments_select_own ON public.payments
    FOR SELECT USING (auth.role() = 'authenticated');

-- service_role 可全权操作 (PostgREST 用 service key 时跳过 RLS)
-- 普通用户不可 INSERT/UPDATE/DELETE（通过应用层 service key 操作）

-- ============================================================
-- 续期存储过程：确认收款时调用，自动累加 VIP 时长
-- 逻辑：如果用户有未到期订阅，在原到期日基础上累加；
--       否则从当前时间开始计算。
-- ============================================================
CREATE OR REPLACE FUNCTION public.confirm_payment_and_renew(
    p_payment_id  BIGINT,
    p_admin_id    BIGINT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_payment  RECORD;
    v_user_id  BIGINT;
    v_plan     VARCHAR;
    v_months   INT;
    v_sub_id   BIGINT;
    v_current_expires TIMESTAMPTZ;
    v_new_expires     TIMESTAMPTZ;
    v_now      TIMESTAMPTZ := now();
BEGIN
    -- 1. 查找订单
    SELECT * INTO v_payment FROM public.payments WHERE id = p_payment_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'error', '订单不存在');
    END IF;
    IF v_payment.status != 'pending' THEN
        RETURN jsonb_build_object('ok', false, 'error', '订单状态非 pending');
    END IF;

    v_user_id := v_payment.user_id;
    v_plan   := v_payment.plan_name;
    v_months := v_payment.months;

    -- 2. 查找当前有效订阅的到期时间
    SELECT expires_at INTO v_current_expires
    FROM public.subscriptions
    WHERE user_id = v_user_id AND status = 'active' AND expires_at > v_now
    ORDER BY expires_at DESC LIMIT 1;

    -- 3. 计算新到期时间
    IF FOUND AND v_current_expires IS NOT NULL THEN
        v_new_expires := v_current_expires + (v_months || ' month')::INTERVAL;
    ELSE
        v_new_expires := v_now + (v_months || ' month')::INTERVAL;
    END IF;

    -- 4. 创建/更新订阅
    INSERT INTO public.subscriptions (user_id, plan_name, status, start_at, expires_at)
    VALUES (v_user_id, v_plan, 'active', v_now, v_new_expires)
    RETURNING id INTO v_sub_id;

    -- 5. 更新订单状态
    UPDATE public.payments
    SET status = 'completed', confirmed_at = v_now, confirmed_by = p_admin_id
    WHERE id = p_payment_id;

    RETURN jsonb_build_object(
        'ok', true,
        'subscription_id', v_sub_id,
        'expires_at', v_new_expires,
        'plan_name', v_plan
    );
END;
$$;

-- 给 service_role 执行存储过程的权限
GRANT EXECUTE ON FUNCTION public.confirm_payment_and_renew(BIGINT, BIGINT) TO service_role;
GRANT ALL ON public.payments TO service_role;

-- ============================================================
-- 执行完毕后 payments 表即可使用
-- ============================================================
