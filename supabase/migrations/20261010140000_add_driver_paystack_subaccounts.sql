-- Store the Paystack recipient account for each driver.
-- Populate this only after that driver's subaccount is created and verified
-- in the matching Paystack mode (test or live).
ALTER TABLE public.oka_rides_drivers
    ADD COLUMN IF NOT EXISTS paystack_subaccount_code text;

CREATE UNIQUE INDEX IF NOT EXISTS oka_rides_drivers_paystack_subaccount_code_unique
    ON public.oka_rides_drivers (paystack_subaccount_code)
    WHERE paystack_subaccount_code IS NOT NULL;
