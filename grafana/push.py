#!/usr/bin/env python3
"""
Script to push Grafana dashboard to Grafana instance via API.

Environment Variables (can be set via .env file or environment):
    GRAFANA_URL: Grafana base URL (e.g., http://localhost:3000)
    GRAFANA_API_KEY: Grafana API key/token for authentication
    GRAFANA_FOLDER_ID: (Optional) Folder ID to place dashboard in
    GRAFANA_FOLDER_NAME: (Optional) Folder name (will create if doesn't exist)
    GRAFANA_OVERWRITE: (Optional) Whether to overwrite existing dashboard (default: true)
    GRAFANA_DASHBOARD_PATH: (Optional) Path to dashboard JSON file (default: split-bot-dashboard.json)

The script will automatically load variables from a .env file in the project root if it exists.
"""
import os
import json
import sys
import requests
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv


def get_env_var(name: str, required: bool = True, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with optional default."""
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Environment variable {name} is required but not set")
    return value


def load_dashboard_json(file_path: str) -> Dict[str, Any]:
    """Load dashboard JSON from file."""
    dashboard_path = Path(file_path)
    if not dashboard_path.exists():
        raise FileNotFoundError(f"Dashboard file not found: {file_path}")
    
    with open(dashboard_path, 'r') as f:
        return json.load(f)


def get_folder_id(grafana_url: str, api_key: str, folder_name: str) -> Optional[int]:
    """Get folder ID by name, or create folder if it doesn't exist."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Try to get existing folder
    response = requests.get(
        f"{grafana_url}/api/folders",
        headers=headers
    )
    
    if response.status_code == 200:
        folders = response.json()
        for folder in folders:
            if folder.get("title") == folder_name:
                return folder.get("id")
    
    # Create folder if it doesn't exist
    create_response = requests.post(
        f"{grafana_url}/api/folders",
        headers=headers,
        json={"title": folder_name}
    )
    
    if create_response.status_code == 200:
        return create_response.json().get("id")
    elif create_response.status_code == 409:
        # Folder already exists, try to get it again
        response = requests.get(
            f"{grafana_url}/api/folders",
            headers=headers
        )
        if response.status_code == 200:
            folders = response.json()
            for folder in folders:
                if folder.get("title") == folder_name:
                    return folder.get("id")
    
    return None


def push_dashboard(
    grafana_url: str,
    api_key: str,
    dashboard: Dict[str, Any],
    folder_id: Optional[int] = None,
    overwrite: bool = True
) -> bool:
    """
    Push dashboard to Grafana via API.
    
    Returns:
        True if successful, False otherwise
    """
    # Remove id and uid to allow Grafana to create new dashboard
    dashboard_payload = dashboard.copy()
    
    # Prepare the API payload
    payload = {
        "dashboard": dashboard_payload,
        "overwrite": overwrite
    }
    
    if folder_id is not None:
        payload["folderId"] = folder_id
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Push dashboard
    response = requests.post(
        f"{grafana_url}/api/dashboards/db",
        headers=headers,
        json=payload
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Dashboard '{result.get('title', 'Unknown')}' pushed successfully!")
        print(f"  URL: {grafana_url}{result.get('url', '')}")
        return True
    else:
        print(f"✗ Failed to push dashboard: {response.status_code}")
        print(f"  Response: {response.text}")
        return False

def load_env_file_if_exists():
    """Load .env file if it exists."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    env_file = project_root / ".env"

    if env_file.exists():
        load_dotenv(env_file, override=False)  # Don't override existing env vars

def main():
    """Main function to push dashboard."""
    try:
        load_env_file_if_exists()
        
        # Get environment variables
        grafana_url = get_env_var("GRAFANA_URL").rstrip('/')
        api_key = get_env_var("GRAFANA_API_KEY")
        folder_id_str = get_env_var("GRAFANA_FOLDER_ID", required=False)
        overwrite_str = get_env_var("GRAFANA_OVERWRITE", required=False, default="true")
        dashboard_path = get_env_var(
            "GRAFANA_DASHBOARD_PATH",
            required=False,
            default=str(Path(__file__).parent / "split-bot-dashboard.json")
        )
        
        # Parse optional parameters
        folder_id = int(folder_id_str) if folder_id_str else None
        overwrite = overwrite_str.lower() in ("true", "1", "yes")
        
        # Check if folder name is provided instead of ID
        folder_name = get_env_var("GRAFANA_FOLDER_NAME", required=False)
        if folder_name and not folder_id:
            print(f"Looking up folder: {folder_name}")
            folder_id = get_folder_id(grafana_url, api_key, folder_name)
            if folder_id:
                print(f"Found folder ID: {folder_id}")
            else:
                print(f"Warning: Could not find or create folder '{folder_name}', proceeding without folder")
        
        # Load dashboard JSON
        print(f"Loading dashboard from: {dashboard_path}")
        dashboard = load_dashboard_json(dashboard_path)
        
        # Push dashboard
        print(f"Pushing dashboard to Grafana at: {grafana_url}")
        success = push_dashboard(
            grafana_url=grafana_url,
            api_key=api_key,
            dashboard=dashboard,
            folder_id=folder_id,
            overwrite=overwrite
        )
        
        sys.exit(0 if success else 1)
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Grafana: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
