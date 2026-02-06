"""Script to get Google OAuth refresh token."""

import os
import sys

def get_refresh_token():
    """Get Google refresh token using OAuth flow."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        print("Please install google-auth-oauthlib:")
        print("  pip install google-auth-oauthlib")
        sys.exit(1)
    
    # Get credentials from user
    client_id = input("Enter Google Client ID: ").strip()
    client_secret = input("Enter Google Client Secret: ").strip()
    
    if not client_id or not client_secret:
        print("Error: Client ID and Secret are required")
        sys.exit(1)
    
    # Create OAuth flow
    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
        }
    }
    
    flow = InstalledAppFlow.from_client_config(client_config, scopes)
    
    print("\nA browser window will open. Please authorize the application.")
    print("If no browser opens, copy the URL and open it manually.\n")
    
    creds = flow.run_local_server(port=0)
    
    print("\n" + "="*60)
    print("SUCCESS! Here are your credentials:")
    print("="*60)
    print(f"\nRefresh Token: {creds.refresh_token}")
    print(f"\nAdd these to your .env files:")
    print(f"  VIVIAN_MCP_GOOGLE_CLIENT_ID={client_id}")
    print(f"  VIVIAN_MCP_GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"  VIVIAN_MCP_GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("\n" + "="*60)

if __name__ == "__main__":
    get_refresh_token()
