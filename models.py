"""
数据模型定义
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()


class SubscriptionStatus(str, enum.Enum):
    """订阅状态枚举"""
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING = "pending"


class PaymentStatus(str, enum.Enum):
    """支付状态枚举"""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    """支付方式枚举"""
    WECHAT = "wechat"
    ALIPAY = "alipay"
    STRIPE = "stripe"


class User(Base):
    """用户模型"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)  # 邮箱验证状态
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"


class Subscription(Base):
    """VIP 订阅模型"""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_name = Column(String(50), nullable=False)  # monthly / quarterly / yearly
    status = Column(SQLEnum(SubscriptionStatus), default=SubscriptionStatus.PENDING)
    start_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    user = relationship("User", back_populates="subscriptions")
    payment = relationship("Payment", back_populates="subscription", uselist=False)

    def __repr__(self):
        return f"<Subscription user_id={self.user_id} plan={self.plan_name} status={self.status}>"

    @property
    def is_active(self) -> bool:
        """检查订阅是否有效"""
        if self.status != SubscriptionStatus.ACTIVE:
            return False
        if self.expires_at is None:
            return False
        return datetime.utcnow() < self.expires_at

    @property
    def days_remaining(self) -> int:
        """剩余天数"""
        if not self.is_active:
            return 0
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)


class Payment(Base):
    """支付记录模型"""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=True, index=True)
    plan_name = Column(String(50), nullable=False)
    amount = Column(Integer, nullable=False)  # 金额（分），存储整数避免浮点精度问题
    currency = Column(String(10), default="CNY")  # CNY / USD
    payment_method = Column(SQLEnum(PaymentMethod), nullable=False)
    status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING)
    transaction_id = Column(String(255), nullable=True, index=True)  # 第三方交易ID
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    user = relationship("User", back_populates="payments")
    subscription = relationship("Subscription", back_populates="payment")

    def __repr__(self):
        return f"<Payment user_id={self.user_id} amount={self.amount} status={self.status}>"

    @property
    def amount_yuan(self) -> float:
        """金额换算为元/美元"""
        return self.amount / 100

    @property
    def amount_display(self) -> str:
        """带货币符号的显示金额"""
        symbol = "¥" if self.currency == "CNY" else "$"
        return f"{symbol}{self.amount_yuan:.0f}"