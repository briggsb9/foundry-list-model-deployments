# Azure AI Foundry Model Deployments Lister

This Python script lists all Azure AI Foundry model deployments across all your Azure subscriptions and outputs the results as both a formatted table and CSV file for reporting.

> [!IMPORTANT]
> **Example Script**: This is a sample implementation to help you get started with programmatically listing Azure AI Foundry deployments. It has NOT been tested for use in production. Customize it to fit your specific requirements or use it for self learning.

## Purpose

The Microsoft Foundry control plane UI currently only allows viewing deployments by clicking through subscriptions one at a time. This script provides a programmatic way to view all your model deployments across all subscriptions in a single report.

## Features

- Scans all accessible Azure subscriptions
- Lists all AI Foundry model deployments
- Outputs formatted console table
- Exports to CSV for reporting
- Matches the UI column structure shown in the Foundry portal

## Prerequisites

1. **Python 3.7 or higher**
2. **Azure CLI** (for authentication) - Install from: https://learn.microsoft.com/cli/azure/install-azure-cli
3. **Azure credentials** with appropriate permissions:
   - Reader access to subscriptions
   - Access to AI Foundry resources

## Installation

1. Clone or download this repository

2. Install the required Python packages:
   ```powershell
   pip install -r requirements.txt
   ```

3. Authenticate with Azure CLI:
   ```powershell
   az login
   ```

## Usage

Simply run the script:

```powershell
python list_foundry_deployments.py
```

The script will:
1. Authenticate using your Azure credentials
2. Discover all accessible subscriptions
3. Scan each subscription for AI Foundry resources
4. Query each resource for model deployments
5. Display results in a formatted table
6. Export to a timestamped CSV file (e.g., `foundry_deployments_20250425_143022.csv`)

## Output

### Console Output
The script displays a formatted table with key columns:
- Name
- Project
- Version
- State
- Base model
- PTU capacity
- Rate limit (tokens per minute)
- Subscription

### CSV Export
The CSV file includes all columns matching the Foundry UI:
- Name
- Project
- Version
- State
- Guardrails
- Deployment type
- PTU capacity
- Rate limit (tokens per minute)
- Base model
- Retirement date
- Fine-tuned
- Model cost
- Deployed
- Subscription
- Resource Group
- Model Publisher
- Connection Name

## Authentication

The script uses `DefaultAzureCredential` which tries multiple authentication methods in order:
1. Environment variables
2. Managed Identity
3. Azure CLI authentication
4. Azure PowerShell
5. Interactive browser

For most users, running `az login` before executing the script is sufficient.

## Troubleshooting

### No deployments found
- Ensure you have AI Foundry projects with active deployments
- Verify you have sufficient permissions (Reader role at minimum)
- Check that you're logged in with the correct Azure account

### Authentication errors
- Run `az login` to authenticate
- Run `az account list` to verify you can access subscriptions
- Ensure your account has access to the subscriptions containing Foundry resources

### API errors
- The script uses the AI Foundry REST API (v1)
- Some resources may not expose the deployments endpoint
- The script will skip resources it cannot access and continue

## Notes

- Only enabled subscriptions are scanned
- The script uses parallel processing (max 20 subscriptions concurrently, 10 accounts per subscription) for performance at scale
- All data comes directly from Azure APIs with no assumptions or fallbacks

### Data Accuracy Considerations

**No Hardcoded Fallbacks**:
- The script uses **only** data returned by the Azure API
- Empty or missing fields are displayed as empty strings, not placeholder values
- This ensures accurate reporting of actual deployment states

**Rate Limits**: 
- Extracted directly from the API's `rateLimits` field
- The API may return multiple rate limit entries or none at all
- The script displays the first valid `count` value found, or `--` if unavailable
- Rate limit structure varies by deployment type and may not always be populated

**Fine-tuned Detection**:
- Identified by checking if the model name starts with `ft:` prefix
- This is a heuristic and may not catch all fine-tuned models if they follow different naming conventions
- Azure's fine-tuning naming format: `ft:base-model:org:custom-suffix:id`

**PTU Capacity**:
- Extracted directly from `sku.capacity` in the API response
- May be empty/null for non-PTU deployments (e.g., pay-as-you-go deployments)

**Deployment Type**:
- Uses the exact SKU name returned by the API (e.g., "Standard", "GlobalStandard", "ProvisionedManaged")
- No transformation or interpretation applied

**Empty Fields**:
- If the API doesn't provide a value, the field will be empty in both console and CSV output
- This applies to: State, Guardrails, Version Upgrade Option, and other optional fields

## API Reference

This script uses the Azure AI Foundry Deployments API:
- Endpoint: `https://learn.microsoft.com/en-us/rest/api/aifoundry/aiprojects/deployments/list`
- API Version: v1