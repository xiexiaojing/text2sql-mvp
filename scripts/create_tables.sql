-- ============================================
-- Text2SQL-MVP 示例数据库表结构
-- 基于 configs/whitelist_tables.yaml 生成
-- ============================================

-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS text2sql_demo DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE text2sql_demo;

-- ============================================
-- 1. 商户表 (merchant)
-- ============================================
CREATE TABLE IF NOT EXISTS merchant (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT 'Merchant ID',
    tenant_id varchar(30) NOT NULL COMMENT 'Tenant ID',
    name VARCHAR(255) NOT NULL COMMENT 'Merchant Name',
    category VARCHAR(100) DEFAULT NULL COMMENT 'Category',
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE' COMMENT 'Status',
    contact_email VARCHAR(255) DEFAULT NULL COMMENT 'Contact Email',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Created At',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated At',
    
    INDEX ix_merchant_tenant_name (tenant_id, name),
    INDEX idx_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Merchant';

-- ============================================
-- 2. 支付订单表 (payment_order)
-- ============================================
CREATE TABLE IF NOT EXISTS payment_order (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT 'Order ID',
    tenant_id varchar(30) NOT NULL COMMENT 'Tenant ID',
    merchant_id BIGINT UNSIGNED NOT NULL COMMENT 'Merchant ID',
    channel VARCHAR(50) NOT NULL COMMENT 'Payment Channel',
    amount DECIMAL(15, 2) NOT NULL DEFAULT 0.00 COMMENT 'Amount',
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING' COMMENT 'Status',
    payer_mobile VARCHAR(20) DEFAULT NULL COMMENT 'Payer Mobile',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Created At',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated At',
    
    INDEX ix_payment_order_tenant_created (tenant_id, created_at),
    INDEX ix_payment_order_tenant_channel (tenant_id, channel),
    INDEX idx_merchant_id (merchant_id),
    INDEX idx_tenant_id (tenant_id),
    
    CONSTRAINT fk_payment_order_merchant FOREIGN KEY (merchant_id) REFERENCES merchant(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Payment Order';

-- ============================================
-- 3. 退款订单表 (refund_order)
-- ============================================
CREATE TABLE IF NOT EXISTS refund_order (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT 'Refund ID',
    tenant_id varchar(30) NOT NULL COMMENT 'Tenant ID',
    payment_order_id BIGINT UNSIGNED NOT NULL COMMENT 'Payment Order ID',
    amount DECIMAL(15, 2) NOT NULL DEFAULT 0.00 COMMENT 'Refund Amount',
    `status` VARCHAR(50) NOT NULL DEFAULT 'PENDING' COMMENT 'Status',
    refund_time TIMESTAMP NULL DEFAULT NULL COMMENT 'Refund Time',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Created At',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated At',
    
    INDEX ix_refund_order_tenant_time (tenant_id, refund_time),
    INDEX idx_payment_order_id (payment_order_id),
    INDEX idx_tenant_id (tenant_id),
    
    CONSTRAINT fk_refund_order_payment FOREIGN KEY (payment_order_id) REFERENCES payment_order(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='Refund Order';

-- ============================================
-- 插入示例数据（可选）
-- ============================================

-- 插入示例商户数据
INSERT INTO merchant (tenant_id, name, category, status, contact_email) VALUES
('demo-tenant-1', '示例商户A', '零售', 'ACTIVE', 'merchant_a@example.com'),
('demo-tenant-1', '示例商户B', '餐饮', 'ACTIVE', 'merchant_b@example.com'),
('demo-tenant-2', '示例商户C', '服务', 'ACTIVE', 'merchant_c@example.com');

-- 插入示例支付订单数据
INSERT INTO payment_order (tenant_id, merchant_id, channel, amount, status, payer_mobile, created_at) VALUES
('demo-tenant-1', 1, 'ALIPAY', 199.99, 'SUCCESS', '13800138000', '2024-01-15 10:30:00'),
('demo-tenant-1', 1, 'WECHAT', 299.50, 'SUCCESS', '13800138001', '2024-01-16 14:20:00'),
('demo-tenant-1', 2, 'ALIPAY', 89.00, 'PENDING', '13800138002', '2024-01-17 09:15:00'),
('demo-tenant-2', 3, 'WECHAT', 499.99, 'SUCCESS', '13800138003', '2024-01-18 16:45:00'),
('demo-tenant-1', 1, 'ALIPAY', 150.00, 'FAILED', '13800138004', '2024-01-19 11:00:00');

-- 插入示例退款订单数据
INSERT INTO refund_order (tenant_id, payment_order_id, amount, status, refund_time) VALUES
('demo-tenant-1', 1, 199.99, 'SUCCESS', '2024-01-20 10:00:00'),
('demo-tenant-1', 2, 100.00, 'PENDING', NULL);
