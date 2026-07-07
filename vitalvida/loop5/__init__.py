"""
Loop 5 — Revenue Growth Engine
==============================

Constitutional boundaries (enforced throughout this package):
  * Loop 5 may READ Loop 4 (customer_relationship) but MUST NEVER write it.
  * Money is only ever created by the immutable chain:
        Business Event -> Achievement Event -> Bonus Event -> Approval -> Payroll
  * "Delivered & Paid" (VV Order.order_status == 'Paid') is the ONLY success gate.
  * One Order, One Order ID, One immutable Upsell Event. Never a replacement order.
  * Upsell commission is a flat, configurable amount, earned ONLY after Delivered & Paid.
  * Cancelled / Returned (RTO) VOIDS earnings via a reversal event, never a delete.

Reuse, never duplicate:
  * Payer            -> vitalvida.payroll.run_monthly_payroll (unchanged; fed via one seam)
  * Approval spine   -> vitalvida.telesales_scoring.calculate_bonus + Bonus Approval Request
  * DPSR             -> vitalvida.dsr.compute_telesales_dsr
  * Settings/ladders -> VV Commission Settings (extended, not replaced)
"""

LOOP5_VERSION = "5.5.1"
