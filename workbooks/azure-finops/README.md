# Azure FinOps Workbook

This folder contains an Azure Monitor Workbook template focused on FinOps visibility and optimization.

## Attribution

This workbook is based on and adapted from work in the original repository:

- https://github.com/ms-sambell/azure-finops-workbook

This folder contains a forked/derived version tailored for this repository's structure and use cases.

## Contents

- `workbook.json`: Workbook template JSON for Azure Monitor Workbooks.

## What This Workbook Covers

- Governance and cost optimization insights
- Reservations and savings plan visibility
- Capacity reservation inventory and utilization views
- ARC and ESU utilization insights

## Import Into Azure Portal

1. Open the Azure portal.
2. Go to **Monitor** > **Workbooks**.
3. Select **+ New** (or open any workbook and choose **Advanced Editor**).
4. Open **Advanced Editor**.
5. Copy all contents from `workbook.json` in this folder.
6. Paste into the editor and select **Apply**.
7. Select **Save As** and save the workbook to your subscription/resource group.

## Recommended Access

Reader access to the target subscription(s) is recommended for most queries. Some cost or billing-related views may require additional permissions depending on your tenant configuration.

## Notes

- This workbook is intended as a discovery and optimization aid.
- Validate section outputs in your tenant and adapt thresholds to your operating model if needed.
