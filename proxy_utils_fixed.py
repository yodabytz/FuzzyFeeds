#!/usr/bin/env python3
"""
Proxy utility module for FuzzyFeeds
Supports SOCKS4, SOCKS5, HTTP, and HTTPS proxies
"""

import socket
import socks
import urllib.request
import urllib.parse
import ssl
import logging
from urllib.parse import urlparse
from config import (
    enable_proxy, proxy_type, proxy_host, proxy_port, 
    proxy_username, proxy_password,
    proxy_irc, proxy_http, proxy_matrix, proxy_discord
)

# Import feeds_only_proxy if it exists, default to False for backward compatibility
try:
    from config import feeds_only_proxy
except ImportError:
    feeds_only_proxy = False

# Import proxy_whitelist if it exists, default to empty list
try:
    from config import proxy_whitelist
except ImportError:
    proxy_whitelist = []

def is_url_whitelisted(url):
    """
    Check if a URL's domain is in the proxy whitelist
    
    Args:
        url: The URL to check
    
    Returns:
        bool: True if the domain should bypass proxy, False otherwise
    """
    if not proxy_whitelist:
        return False
    
    try:
        domain = urlparse(url).netloc.lower()
        # Remove port if present
        if ':' in domain:
            domain = domain.split(':')[0]
        
        # Check if domain or any parent domain is in whitelist
        for whitelisted_domain in proxy_whitelist:
            whitelisted_domain = whitelisted_domain.lower()
            if domain == whitelisted_domain or domain.endswith('.' + whitelisted_domain):
                logging.info(f"URL {url} bypassing proxy (whitelisted domain: {whitelisted_domain})")
                return True
        
        return False
    except Exception as e:
        logging.error(f"Error checking whitelist for {url}: {e}")
        return False

def create_proxy_socket(connection_type="general"):
    """
    Create a socket with proxy support based on configuration
    
    Args:
        connection_type: "irc", "http", "matrix", "discord", or "general"
    
    Returns:
        socket object configured with proxy settings
    """
    if not enable_proxy:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Check if this connection type should use proxy
    use_proxy = True
    
    # If feeds_only_proxy is enabled, only allow HTTP connections through proxy
    if feeds_only_proxy:
        if connection_type != "http":
            use_proxy = False
    else:
        # Normal proxy routing based on individual settings
        if connection_type == "irc" and not proxy_irc:
            use_proxy = False
        elif connection_type == "http" and not proxy_http:
            use_proxy = False
        elif connection_type == "matrix" and not proxy_matrix:
            use_proxy = False
        elif connection_type == "discord" and not proxy_discord:
            use_proxy = False
    
    if not use_proxy:
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Create proxy socket
    if proxy_type.lower() == "socks5":
        proxy_socket = socks.socksocket()
        if proxy_username and proxy_password:
            proxy_socket.set_proxy(socks.SOCKS5, proxy_host, proxy_port, 
                                 username=proxy_username, password=proxy_password)
        else:
            proxy_socket.set_proxy(socks.SOCKS5, proxy_host, proxy_port)
    elif proxy_type.lower() == "socks4":
        proxy_socket = socks.socksocket()
        proxy_socket.set_proxy(socks.SOCKS4, proxy_host, proxy_port)
    elif proxy_type.lower() in ["http", "https"]:
        proxy_socket = socks.socksocket()
        proxy_socket.set_proxy(socks.HTTP, proxy_host, proxy_port, 
                             username=proxy_username, password=proxy_password)
    else:
        logging.error(f"Unsupported proxy type: {proxy_type}")
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    logging.info(f"Created {proxy_type.upper()} proxy socket for {connection_type} connections")
    return proxy_socket

def create_proxy_ssl_context(connection_type="general"):
    """
    Create SSL context with proxy support
    
    Args:
        connection_type: "irc", "http", "matrix", "discord", or "general"
    
    Returns:
        SSL context configured for proxy use
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context

def wrap_socket_with_proxy(raw_socket, server_hostname, connection_type="general"):
    """
    Wrap a raw socket with SSL and proxy support
    
    Args:
        raw_socket: The raw socket to wrap
        server_hostname: Hostname for SSL verification
        connection_type: Type of connection for proxy routing
    
    Returns:
        SSL-wrapped socket
    """
    context = create_proxy_ssl_context(connection_type)
    return context.wrap_socket(raw_socket, server_hostname=server_hostname)

def create_proxy_opener(url=None):
    """
    Create urllib opener with proxy support for HTTP requests
    
    Args:
        url: Optional URL to check against whitelist
    
    Returns:
        urllib.request.OpenerDirector configured with proxy
    """
    # Check if URL is whitelisted (should bypass proxy)
    if url and is_url_whitelisted(url):
        logging.info(f"Creating direct opener for whitelisted URL: {url}")
        return urllib.request.build_opener()
    
    # For HTTP requests, check if we should use proxy
    if not enable_proxy:
        return urllib.request.build_opener()
    
    # If feeds_only_proxy is enabled, always use proxy for HTTP
    # If feeds_only_proxy is disabled, check proxy_http setting
    if not feeds_only_proxy and not proxy_http:
        return urllib.request.build_opener()
    
    # Build proxy URL
    if proxy_username and proxy_password:
        auth_string = f"{proxy_username}:{proxy_password}@"
    else:
        auth_string = ""
    
    # For SOCKS proxies, use requests with PySocks instead of urllib
    if proxy_type.lower().startswith("socks"):
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from requests.packages.urllib3.util.retry import Retry
            
            # Create a session with SOCKS proxy
            session = requests.Session()
            
            if proxy_type.lower() == "socks5":
                proxy_url = f"socks5://{auth_string}{proxy_host}:{proxy_port}"
            else:
                proxy_url = f"socks4://{auth_string}{proxy_host}:{proxy_port}"
            
            session.proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            
            # Create a custom opener that uses the requests session
            class RequestsOpener:
                def __init__(self, session):
                    self.session = session
                
                def open(self, request, timeout=10):
                    # Convert urllib.request.Request to requests call
                    url = request.full_url
                    headers = dict(request.headers)
                    
                    response = self.session.get(url, headers=headers, timeout=timeout)
                    
                    # Create a file-like object that urllib expects
                    class ResponseAdapter:
                        def __init__(self, response):
                            self.response = response
                            self.status = response.status_code
                        
                        def read(self):
                            return self.response.content
                        
                        def decode(self, encoding='utf-8'):
                            return self.response.content.decode(encoding)
                    
                    return ResponseAdapter(response)
            
            logging.info(f"Created {proxy_type.upper()} proxy opener using requests session")
            return RequestsOpener(session)
            
        except ImportError:
            logging.error("requests module required for SOCKS proxy support")
            return urllib.request.build_opener()
    
    # For HTTP proxies
    if proxy_type.lower() == "http":
        proxy_url = f"http://{auth_string}{proxy_host}:{proxy_port}"
    elif proxy_type.lower() == "https":
        proxy_url = f"https://{auth_string}{proxy_host}:{proxy_port}"
    else:
        logging.error(f"Unsupported proxy type for HTTP: {proxy_type}")
        return urllib.request.build_opener()
    
    proxy_handler = urllib.request.ProxyHandler({
        'http': proxy_url,
        'https': proxy_url
    })
    
    opener = urllib.request.build_opener(proxy_handler)
    logging.info(f"Created HTTP proxy opener with {proxy_type.upper()} proxy")
    return opener

def test_proxy_connection():
    """
    Test proxy connectivity
    
    Returns:
        bool: True if proxy is working, False otherwise
    """
    if not enable_proxy:
        logging.info("Proxy disabled, direct connection test passed")
        return True
    
    try:
        # Test with a simple HTTP request
        opener = create_proxy_opener()
        if hasattr(opener, 'open'):
            response = opener.open(urllib.request.Request("http://httpbin.org/ip"), timeout=10)
            if hasattr(response, 'read'):
                data = response.read()
                if hasattr(data, 'decode'):
                    data = data.decode('utf-8')
                elif hasattr(response, 'response'):
                    data = response.response.text
                logging.info(f"Proxy test successful: {data.strip()}")
                return True
        return False
    except Exception as e:
        logging.error(f"Proxy test failed: {e}")
        return False

def log_proxy_status():
    """Log current proxy configuration"""
    if not enable_proxy:
        logging.info("Proxy: DISABLED - All connections direct")
        return
    
    logging.info(f"Proxy: ENABLED - {proxy_type.upper()} proxy at {proxy_host}:{proxy_port}")
    
    if feeds_only_proxy:
        logging.info("Proxy mode: FEEDS ONLY - Only RSS/HTTP requests use proxy")
        logging.info("IRC, Matrix, and Discord connections: DIRECT")
    else:
        logging.info(f"Proxy routing - IRC: {proxy_irc}, HTTP: {proxy_http}, Matrix: {proxy_matrix}, Discord: {proxy_discord}")
    
    if proxy_username:
        logging.info("Proxy authentication: ENABLED")
    else:
        logging.info("Proxy authentication: DISABLED")
    
    if proxy_whitelist:
        logging.info(f"Proxy whitelist: {proxy_whitelist}")