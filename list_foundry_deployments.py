#!/usr/bin/env python3
"""
Azure AI Foundry Model Deployments Lister

This script lists all Azure AI Foundry model deployments across all subscriptions
and outputs the results as a formatted table and CSV file.
"""

import sys
import csv
from datetime import datetime
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import SubscriptionClient

# Constants
COGNITIVESERVICES_API_VERSION = "2025-06-01"  # AI Foundry-aware version


class FoundryDeploymentLister:
    """Class to list Azure AI Foundry model deployments across subscriptions."""
    
    def __init__(self):
        """Initialize the lister with Azure credentials."""
        # DefaultAzureCredential tries multiple authentication methods in order:
        # 1. Environment variables, 2. Managed Identity, 3. Azure CLI, 4. Azure PowerShell
        # This makes the script work in various environments (local dev, CI/CD, Azure VMs)
        self.credential = DefaultAzureCredential()
        self._get_token()
        
    def _get_token(self):
        """Get access token for Azure management API.
        
        Note: Tokens typically expire after 1 hour. For long-running scripts,
        consider implementing token refresh logic.
        """
        try:
            # Get access token specifically for Azure Resource Manager (management.azure.com)
            # This scope allows us to list subscriptions, resource groups, and query resource properties
            # The ".default" suffix requests all permissions the app has been granted
            token_obj = self.credential.get_token("https://management.azure.com/.default")
            self.management_token = token_obj.token
            
            print("Successfully authenticated")
        except Exception as e:
            print(f"Error getting authentication token: {e}")
            print("Ensure you're logged in with 'az login' or have appropriate credentials configured.")
            sys.exit(1)
    
    def get_subscriptions(self) -> List[Dict[str, str]]:
        """Get all accessible Azure subscriptions."""
        print("Fetching subscriptions...")
        try:
            # Use the Azure SDK's SubscriptionClient to discover all subscriptions
            # the authenticated identity has access to (Reader role or higher)
            subscription_client = SubscriptionClient(self.credential)
            subscriptions = []
            
            for sub in subscription_client.subscriptions.list():
                subscriptions.append({
                    'id': sub.subscription_id,
                    'name': sub.display_name,
                    'state': sub.state
                })
                print(f"  Found: {sub.display_name} ({sub.subscription_id})")
            
            return subscriptions
        except Exception as e:
            print(f"Error fetching subscriptions: {e}")
            return []
    
    def get_cognitive_services_accounts(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Get all Cognitive Services accounts (AI hubs) in a subscription."""
        try:
            # Direct REST API call to list all Cognitive Services accounts in the subscription
            # Using 2025-06-01 API version which supports both OpenAI and AI Foundry resources
            # Pattern: /subscriptions/{subscriptionId}/providers/{resourceProvider}/accounts
            url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.CognitiveServices/accounts"
            params = {"api-version": COGNITIVESERVICES_API_VERSION}
            headers = {"Authorization": f"Bearer {self.management_token}"}
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code == 200:
                accounts = []
                data = response.json()
                for account in data.get('value', []):
                    kind = account.get('kind', '')
                    # Filter for account types that can have model deployments:
                    # - 'OpenAI': Classic Azure OpenAI resources
                    # - 'AIServices': Newer AI Foundry resources (includes multi-service accounts)
                    if kind in ['AIServices', 'OpenAI']:
                        # Parse resource group from Azure resource ID
                        # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/...
                        resource_id = account.get('id', '')
                        resource_group = ''
                        if resource_id:
                            parts = resource_id.split('/')
                            # Find 'resourceGroups' and get the next element
                            try:
                                rg_index = parts.index('resourceGroups')
                                if rg_index + 1 < len(parts):
                                    resource_group = parts[rg_index + 1]
                            except (ValueError, IndexError):
                                pass  # Keep empty string if parsing fails
                        
                        accounts.append({
                            'id': account.get('id'),
                            'name': account.get('name'),
                            'resource_group': resource_group,
                            'location': account.get('location'),
                            'kind': kind,
                            'properties': account.get('properties', {}),
                            'type': account.get('type')
                        })
                return accounts
            return []
        except Exception as e:
            print(f"  Error fetching Cognitive Services accounts: {e}")
            return []
    
    def _process_subscription(self, sub: Dict[str, str]) -> List[Dict[str, Any]]:
        """Process a single subscription and return all deployments found.
        
        This method is designed to run in parallel (called by ThreadPoolExecutor).
        Each subscription is processed independently to improve performance.
        """
        deployments = []
        
        if sub['state'] != 'Enabled':
            print(f"Skipping disabled subscription: {sub['name']}")
            return deployments
        
        print(f"Scanning subscription: {sub['name']} ({sub['id']})")
        
        # Get Cognitive Services accounts
        cognitive_accounts = self.get_cognitive_services_accounts(sub['id'])
        
        if not cognitive_accounts:
            print(f"  No AI Foundry or OpenAI resources found in this subscription.")
            return deployments
        
        print(f"  Found {len(cognitive_accounts)} Cognitive Services account(s)")
        
        # Process accounts in parallel within this subscription
        # Nested parallelization: Each subscription processes its accounts concurrently
        # Limit to 10 concurrent account queries to avoid overwhelming the API
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_account = {
                executor.submit(
                    self._process_account,
                    sub,
                    account
                ): account for account in cognitive_accounts
            }
            
            for future in as_completed(future_to_account):
                try:
                    account_deployments = future.result()
                    deployments.extend(account_deployments)
                except Exception as e:
                    account = future_to_account[future]
                    print(f"    Error processing account {account['name']}: {e}")
        
        return deployments
    
    def _process_account(self, sub: Dict[str, str], account: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process a single account and return all deployments found."""
        deployments = []
        
        print(f"    Checking account: {account['name']} (kind: {account['kind']})")
        
        if account['kind'] in ('OpenAI', 'AIServices'):
            print(f"      Listing deployments via management API (api-version={COGNITIVESERVICES_API_VERSION})")
            
            account_deployments = self.get_account_deployments(
                sub['id'],
                account['resource_group'],
                account['name']
            )
            
            if account_deployments:
                print(f"        Found {len(account_deployments)} deployment(s)")
                for deployment in account_deployments:
                    deployment['subscription_id'] = sub['id']
                    deployment['subscription_name'] = sub['name']
                    deployment['resource_name'] = account['name']
                    deployment['resource_group'] = account['resource_group']
                    deployment['account_kind'] = account['kind']
                    deployments.append(deployment)
            else:
                print(f"        No deployments found")
        
        return deployments
    
    def get_account_deployments(self, subscription_id: str, resource_group: str, account_name: str) -> List[Dict[str, Any]]:
        """Get deployments from a Cognitive Services account (OpenAI or AIServices) using management API.
        
        This uses the 2025-06-01 API version which supports both:
        - Classic Azure OpenAI deployments
        - Newer AI Foundry deployments
        
        Key API pattern: The deployments endpoint is accessed via the account resource path
        followed by /deployments. This returns all model deployments for that account.
        """
        try:
            # Construct the full resource path to the deployments collection
            # Pattern: /subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/accounts/{name}/deployments
            url = f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.CognitiveServices/accounts/{account_name}/deployments"
            params = {"api-version": COGNITIVESERVICES_API_VERSION}
            headers = {"Authorization": f"Bearer {self.management_token}"}
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                deployments = []
                
                for item in data.get('value', []):
                    # Extract deployment data from API response
                    # Response structure: item.properties contains deployment config
                    # item.sku contains capacity/pricing tier information
                    # We extract only what the API provides - no defaults or assumptions
                    props = item.get('properties', {})
                    model = props.get('model', {})
                    sku = item.get('sku', {})
                    model_name = model.get('name', '')
                    model_version = model.get('version', '')
                    
                    # Detect fine-tuned models using naming convention heuristic
                    # Azure OpenAI fine-tuned models follow the pattern: "ft:base-model:org:custom-suffix:id"
                    # This is a simple check - adjust if Azure changes their naming convention
                    # Note: This is client-side logic since the API doesn't provide a dedicated flag
                    is_fine_tuned = model_name.startswith('ft:')
                    
                    deployments.append({
                        'name': item.get('name', ''),
                        'type': 'ModelDeployment',
                        'modelName': model_name,
                        'modelVersion': model_version,
                        'modelFormat': model.get('format', ''),
                        'versionUpgradeOption': props.get('versionUpgradeOption', ''),
                        'sku': {
                            'name': sku.get('name', ''),
                            'capacity': sku.get('capacity')
                        },
                        'capabilities': props.get('capabilities', {}),
                        'provisioningState': props.get('provisioningState', ''),
                        'raiPolicyName': props.get('raiPolicyName', ''),
                        'rateLimits': props.get('rateLimits', []),
                        'systemData': item.get('systemData', {}),
                        'isFineTuned': is_fine_tuned
                    })
                
                return deployments
            elif response.status_code == 404:
                return []
            else:
                print(f"        Warning: deployments list failed (HTTP {response.status_code}) for {account_name}")
                return []
        except Exception as e:
            print(f"        Error fetching account deployments: {e}")
            return []
    
    def list_all_deployments(self) -> List[Dict[str, Any]]:
        """List all deployments across all subscriptions and projects using parallel processing."""
        all_deployments = []
        
        subscriptions = self.get_subscriptions()
        
        if not subscriptions:
            print("No subscriptions found or unable to access subscriptions.")
            return []
        
        print(f"\nScanning {len(subscriptions)} subscription(s) for AI Foundry deployments...\n")
        
        # Process subscriptions in parallel for performance at scale
        # With hundreds of subscriptions, sequential processing would take too long
        # Limit to 20 concurrent subscriptions to balance speed with API rate limits
        max_workers = min(20, len(subscriptions))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sub = {
                executor.submit(self._process_subscription, sub): sub 
                for sub in subscriptions
            }
            
            for future in as_completed(future_to_sub):
                try:
                    deployments = future.result()
                    all_deployments.extend(deployments)
                except Exception as e:
                    sub = future_to_sub[future]
                    print(f"Error processing subscription {sub['name']}: {e}")
        
        return all_deployments
    
    def format_deployments_for_output(self, deployments: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Format deployment data for output matching the UI screenshot."""
        formatted = []
        
        for dep in deployments:
            # Extract deployment details based on API response structure
            sku = dep.get('sku', {})
            system_data = dep.get('systemData', {})
            
            # Format deployment date
            created_at = system_data.get('createdAt', '')
            deployed_date = created_at.split('T')[0] if created_at else ''
            
            # Determine if fine-tuned
            is_fine_tuned = 'Yes' if dep.get('isFineTuned', False) else 'No'
            
            # Handle PTU capacity - could be None, 0, or a positive integer
            capacity = sku.get('capacity')
            capacity_str = str(capacity) if capacity is not None else ''
            
            formatted_dep = {
                'Name': dep.get('name', ''),
                'Project': dep.get('resource_name', ''),
                'Version': dep.get('modelVersion', ''),
                'Version Upgrade Option': dep.get('versionUpgradeOption', ''),
                'State': dep.get('provisioningState', ''),
                'Guardrails': dep.get('raiPolicyName', ''),
                'Deployment type': sku.get('name', ''),
                'PTU capacity': capacity_str,
                'Rate limit (tokens per minute)': self._get_rate_limit(dep),
                'Base model': dep.get('modelName', ''),
                'Fine-tuned': is_fine_tuned,
                'Deployed': deployed_date,
                'Subscription': dep.get('subscription_name', ''),
                'Resource Group': dep.get('resource_group', ''),
                'Resource Type': dep.get('account_kind', '')
            }
            
            formatted.append(formatted_dep)
        
        return formatted
    
    def _get_rate_limit(self, deployment: Dict[str, Any]) -> str:
        """Extract rate limit from API response.
        
        The API returns rateLimits as an array that may contain multiple limits
        (e.g., per-minute, per-day). Structure varies by deployment type.
        We extract the first valid 'count' value found.
        """
        rate_limits = deployment.get('rateLimits', [])
        
        if not rate_limits:
            return '--'  # Only placeholder we use - indicates API provided no rate limit data
        
        # Iterate through rate limit entries to find the first valid count
        # Common fields in each entry: count (the limit value), renewalPeriod, key
        for limit in rate_limits:
            if isinstance(limit, dict):
                # Common fields: count, renewalPeriod, key
                count = limit.get('count')
                if count:
                    return f"{count:,}"
        
        return '--'
    
    def write_to_csv(self, deployments: List[Dict[str, str]], filename: str = 'foundry_deployments.csv'):
        """Write deployments to a CSV file."""
        if not deployments:
            print("No deployments to write.")
            return
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = list(deployments[0].keys())
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                writer.writerows(deployments)
            
            print(f"\nCSV file written: {filename}")
        except Exception as e:
            print(f"Error writing CSV file: {e}")
    
    def print_table(self, deployments: List[Dict[str, str]]):
        """Print deployments as a formatted table."""
        if not deployments:
            print("No deployments found.")
            return
        
        # Key columns to display in console (subset for readability)
        display_columns = ['Name', 'Project', 'Version', 'Base model', 
                          'Rate limit (tokens per minute)', 'Deployed', 'Resource Group', 'Subscription']
        
        # Calculate column widths
        col_widths = {}
        for col in display_columns:
            col_widths[col] = max(
                len(col),
                max(len(str(dep.get(col, ''))) for dep in deployments)
            )
        
        # Print header
        print("\n" + "=" * (sum(col_widths.values()) + len(display_columns) * 3))
        header = " | ".join(col.ljust(col_widths[col]) for col in display_columns)
        print(header)
        print("=" * (sum(col_widths.values()) + len(display_columns) * 3))
        
        # Print rows
        for dep in deployments:
            row = " | ".join(str(dep.get(col, '')).ljust(col_widths[col]) for col in display_columns)
            print(row)
        
        print("=" * (sum(col_widths.values()) + len(display_columns) * 3))
        print(f"\nTotal deployments found: {len(deployments)}")
        print("\nNote: Full details including State, PTU capacity, Guardrails, and more are available in the CSV output.")


def main():
    """Main function to run the deployment lister."""
    print("=" * 80)
    print("Azure AI Foundry Model Deployments Lister")
    print("=" * 80)
    print()
    
    try:
        lister = FoundryDeploymentLister()
        
        # Get all deployments
        deployments = lister.list_all_deployments()
        
        if not deployments:
            print("\n⚠ No deployments found across any subscriptions.")
            print("\nPossible reasons:")
            print("  - No AI Foundry projects with deployments exist")
            print("  - Insufficient permissions to access resources")
            print("  - Authentication issues")
            return
        
        # Format deployments
        formatted_deployments = lister.format_deployments_for_output(deployments)
        
        # Display results
        lister.print_table(formatted_deployments)
        
        # Write to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"foundry_deployments_{timestamp}.csv"
        lister.write_to_csv(formatted_deployments, csv_filename)
        
        print(f"\nSuccessfully listed {len(deployments)} deployment(s)")
        
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
