"""
Gym Application Module

This module provides the main Flask application factory and configuration
for the Gym management system.
"""

import logging
import logging.handlers
import os
from datetime import timedelta
from functools import wraps
from typing import Optional, Callable, Any

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import safe_str_cmp
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create logs directory if it doesn't exist
logs_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(logs_dir, exist_ok=True)

# File handler with rotation
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(logs_dir, 'gym_app.log'),
    maxBytes=10485760,  # 10MB
    backupCount=10
)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)


def create_app(config_name: Optional[str] = None) -> Flask:
    """
    Application factory function to create and configure the Flask app.
    
    Args:
        config_name: Optional configuration name (development, testing, production)
        
    Returns:
        Flask: Configured Flask application instance
        
    Raises:
        ValueError: If configuration is invalid
    """
    try:
        app = Flask(__name__)
        
        # Load configuration
        env = os.getenv('FLASK_ENV', 'development')
        config_name = config_name or env
        
        if config_name == 'development':
            app.config.from_object('gym.config.DevelopmentConfig')
        elif config_name == 'testing':
            app.config.from_object('gym.config.TestingConfig')
        elif config_name == 'production':
            app.config.from_object('gym.config.ProductionConfig')
        else:
            raise ValueError(f"Invalid configuration: {config_name}")
        
        # Security configurations
        app.config['SESSION_COOKIE_SECURE'] = app.config.get('SECURE_COOKIES', True)
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
        
        # CORS configuration
        cors_origins = os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')
        CORS(app, resources={r"/api/*": {"origins": cors_origins}})
        
        # Rate limiting
        limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            default_limits=["200 per day", "50 per hour"],
            storage_uri=os.getenv('REDIS_URL', 'memory://')
        )
        
        # Register error handlers
        register_error_handlers(app)
        
        # Register before request handlers
        register_before_request_handlers(app)
        
        # Register blueprints
        register_blueprints(app)
        
        logger.info(f"Application created successfully in {config_name} mode")
        return app
        
    except Exception as e:
        logger.error(f"Failed to create application: {str(e)}", exc_info=True)
        raise


def register_error_handlers(app: Flask) -> None:
    """
    Register error handlers for the application.
    
    Args:
        app: Flask application instance
    """
    @app.errorhandler(400)
    def bad_request(error: Exception) -> tuple[dict, int]:
        """Handle 400 Bad Request errors."""
        logger.warning(f"Bad request: {str(error)}")
        return jsonify({
            'error': 'Bad Request',
            'message': str(error.description) if hasattr(error, 'description') else 'Invalid request'
        }), 400
    
    @app.errorhandler(401)
    def unauthorized(error: Exception) -> tuple[dict, int]:
        """Handle 401 Unauthorized errors."""
        logger.warning(f"Unauthorized access attempt: {request.remote_addr}")
        return jsonify({
            'error': 'Unauthorized',
            'message': 'Authentication required'
        }), 401
    
    @app.errorhandler(403)
    def forbidden(error: Exception) -> tuple[dict, int]:
        """Handle 403 Forbidden errors."""
        logger.warning(f"Forbidden access attempt: {request.remote_addr}")
        return jsonify({
            'error': 'Forbidden',
            'message': 'You do not have permission to access this resource'
        }), 403
    
    @app.errorhandler(404)
    def not_found(error: Exception) -> tuple[dict, int]:
        """Handle 404 Not Found errors."""
        logger.debug(f"Resource not found: {request.path}")
        return jsonify({
            'error': 'Not Found',
            'message': 'The requested resource does not exist'
        }), 404
    
    @app.errorhandler(429)
    def rate_limit_exceeded(error: Exception) -> tuple[dict, int]:
        """Handle 429 Too Many Requests errors."""
        logger.warning(f"Rate limit exceeded for {request.remote_addr}")
        return jsonify({
            'error': 'Too Many Requests',
            'message': 'Rate limit exceeded. Please try again later.'
        }), 429
    
    @app.errorhandler(500)
    def internal_server_error(error: Exception) -> tuple[dict, int]:
        """Handle 500 Internal Server Error."""
        logger.error(f"Internal server error: {str(error)}", exc_info=True)
        return jsonify({
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred. Please try again later.'
        }), 500
    
    @app.errorhandler(Exception)
    def handle_exception(error: Exception) -> tuple[dict, int]:
        """Handle unexpected exceptions."""
        logger.error(f"Unhandled exception: {str(error)}", exc_info=True)
        return jsonify({
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred'
        }), 500


def register_before_request_handlers(app: Flask) -> None:
    """
    Register before_request handlers for the application.
    
    Args:
        app: Flask application instance
    """
    @app.before_request
    def log_request_info() -> None:
        """Log incoming request information."""
        g.start_time = request.start_time
        logger.debug(f"{request.method} {request.path} from {request.remote_addr}")
    
    @app.before_request
    def validate_request_headers() -> Optional[tuple[dict, int]]:
        """Validate request headers for security."""
        # Validate Content-Type for POST/PUT requests
        if request.method in ['POST', 'PUT']:
            if not request.is_json:
                logger.warning(f"Invalid Content-Type from {request.remote_addr}")
                return jsonify({
                    'error': 'Bad Request',
                    'message': 'Content-Type must be application/json'
                }), 400
        return None


def register_blueprints(app: Flask) -> None:
    """
    Register blueprints with the application.
    
    Args:
        app: Flask application instance
    """
    try:
        # Import blueprints
        from gym.routes import api_bp, health_bp
        
        # Register blueprints
        app.register_blueprint(health_bp)
        app.register_blueprint(api_bp, url_prefix='/api')
        
        logger.info("Blueprints registered successfully")
    except ImportError as e:
        logger.error(f"Failed to import blueprints: {str(e)}", exc_info=True)
        raise


def token_required(f: Callable) -> Callable:
    """
    Decorator to require authentication token.
    
    Args:
        f: Function to decorate
        
    Returns:
        Decorated function
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        token = None
        
        # Check for token in Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                logger.warning("Malformed Authorization header")
                return jsonify({'error': 'Invalid token format'}), 401
        
        if not token:
            logger.warning(f"Token missing from {request.remote_addr}")
            return jsonify({'error': 'Token is missing'}), 401
        
        try:
            # Validate token (implement your token validation logic here)
            valid_token = os.getenv('API_TOKEN')
            if not valid_token or not safe_str_cmp(token, valid_token):
                logger.warning(f"Invalid token attempt from {request.remote_addr}")
                return jsonify({'error': 'Invalid token'}), 401
            
            g.user_token = token
            return f(*args, **kwargs)
        
        except Exception as e:
            logger.error(f"Token validation error: {str(e)}", exc_info=True)
            return jsonify({'error': 'Token validation failed'}), 401
    
    return decorated


if __name__ == '__main__':
    try:
        app = create_app()
        debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
        port = int(os.getenv('PORT', 5000))
        
        logger.info(f"Starting Gym application on port {port}")
        app.run(
            host='0.0.0.0',
            port=port,
            debug=debug_mode,
            use_reloader=debug_mode
        )
    except Exception as e:
        logger.critical(f"Failed to start application: {str(e)}", exc_info=True)
        raise
