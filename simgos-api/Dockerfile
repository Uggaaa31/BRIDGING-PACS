# ==============================================================================
# Dockerfile - SIMGOS REST API Endpoint (PHP 8.2 + Apache)
# ==============================================================================
FROM php:8.2-apache

# Install ekstensi PDO MySQL
RUN docker-php-ext-install pdo pdo_mysql

# Aktifkan modul Apache rewrite dan headers
RUN a2enmod rewrite headers

# Izinkan .htaccess Override pada direktori /var/www/
RUN sed -ri -e 's!AllowOverride None!AllowOverride All!g' /etc/apache2/apache2.conf

# Set working directory
WORKDIR /var/www/html

# Salin source code API
COPY . /var/www/html/

# Pastikan folder logs ada dan memiliki permission write
RUN mkdir -p /var/www/html/logs \
    && chown -R www-data:www-data /var/www/html \
    && chmod -R 755 /var/www/html \
    && chmod -R 775 /var/www/html/logs

# Log API disimpan di luar DocumentRoot agar tidak bisa diakses lewat HTTP
RUN mkdir -p /var/log/simgos \
    && chown -R www-data:www-data /var/log/simgos \
    && chmod 775 /var/log/simgos

# Expose HTTP port
EXPOSE 80

# Apache berjalan di foreground secara otomatis
CMD ["apache2-foreground"]
