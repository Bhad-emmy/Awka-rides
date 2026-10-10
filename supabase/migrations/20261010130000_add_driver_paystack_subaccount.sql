-- Store the Paystack subaccount assigned to each driver.
-- Subaccounts are created in Paystack Dashboard; the code is assigned by an authenticated dispatcher.
ALTER TABLE public.oka_rides_drivers
    ADD COLUMN IF NOT EXISTS paystack_subaccount_code text;

ALTER TABLE public.oka_rides_drivers
    DROP CONSTRAINT IF EXISTS oka_rides_drivers_paystack_subaccount_code_check;

ALTER TABLE public.oka_rides_drivers
    ADD CONSTRAINT oka_rides_drivers_paystack_subaccount_code_check
    CHECK (
        paystack_subaccount_code IS NULL
        OR paystack_subaccount_code ~ '^ACCT_[A-Za-z0-9]+$'
    );

COMMENT ON COLUMN public.oka_rides_drivers.paystack_subaccount_code IS
    'Paystack subaccount code created in the same environment (test/live) as PAYSTACK_SECRET_KEY.';
