from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import stripe
from datetime import datetime, timedelta
from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import os

app = Flask(__name__)

stripe.api_key = ''

app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USERNAME'] = 'your email'
app.config['MAIL_PASSWORD'] = 'mail password'
app.config['MAIL_DEBUG'] = True
app.config['MAIL_DEFAULT_SENDER'] = 'default mail id'

db = SQLAlchemy(app)
mail = Mail(app)

class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    subscription_id = db.Column(db.String(50), primary_key=True)
    customer_id = db.Column(db.String(50), nullable=False)
    customer_name = db.Column(db.String(100), nullable=True)
    customer_email = db.Column(db.String(120), nullable=True)
    product_name = db.Column(db.String(100), nullable=True)
    plan_id = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
    expiry_date = db.Column(db.DateTime, nullable=True)

    def __init__(self, subscription_id, customer_id, customer_name, customer_email, product_name, plan_id, status, created_at, updated_at, expiry_date):
        self.subscription_id = subscription_id
        self.customer_id = customer_id
        self.customer_name = customer_name
        self.customer_email = customer_email
        self.product_name = product_name
        self.plan_id = plan_id
        self.status = status
        self.created_at = created_at
        self.updated_at = updated_at
        self.expiry_date = expiry_date

with app.app_context():
    db.create_all()


scheduler = BackgroundScheduler()
scheduler.start()

def send_email_alert(email, subscription_id, end_date):
    with app.app_context():
        try:
            days_before_end = 3  
            alert_date = end_date - timedelta(days=days_before_end)
            current_time = datetime.utcnow()
            #print(f"Current time: {current_time}, Alert date: {alert_date}, Subscription end date: {end_date}")

            if current_time >= alert_date:
                msg = Message(
                    subject='Subscription Expiry Alert',
                    recipients=[email],
                    body=f'Your subscription {subscription_id} is about to expire on {end_date.strftime("%Y-%m-%d")}.'
                )
                mail.send(msg)
                #print(f"Alert email sent to {email} for subscription {subscription_id}")
        except Exception as e:
            print(f"Error sending email alert: {e}")

def check_subscriptions():
    with app.app_context():
        try:
            subscriptions = stripe.Subscription.list(status='active')
            for subscription in subscriptions.auto_paging_iter():
                end_date = datetime.fromtimestamp(subscription.current_period_end)
                customer = stripe.Customer.retrieve(subscription.customer)
                email = customer.email
                send_email_alert(email, subscription.id, end_date)
        except Exception as e:
            print(f"Error checking subscriptions: {e}")


scheduler.add_job(func=check_subscriptions, trigger='interval', days=1)
print("Manual check triggered")
check_subscriptions()

@app.route('/store_subscriptions', methods=['GET'])
def store_subscriptions():
    try:
        subscriptions = stripe.Subscription.list(limit=100)
    except stripe.error.StripeError as e:
        print(f"Error fetching subscriptions from Stripe: {e}")
        return jsonify({"error": "Failed to fetch subscriptions from Stripe"})

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = subscription.id
        customer_id = subscription.customer
        plan_id = subscription.plan.id if subscription.plan else None
        status = subscription.status
        created_at = datetime.fromtimestamp(subscription.created)
        updated_at = datetime.fromtimestamp(subscription.current_period_end)

        try:
            customer = stripe.Customer.retrieve(customer_id)
            customer_name = customer.name
            customer_email = customer.email
        except stripe.error.StripeError as e:
            print(f"Error fetching customer details from Stripe: {e}")
            customer_name = None
            customer_email = None

        product_name = None
        if subscription.plan:
            try:
                product = stripe.Product.retrieve(subscription.plan.product)
                product_name = product.name
            except stripe.error.StripeError as e:
                print(f"Error fetching product details from Stripe: {e}")

        if status == 'active':
            expiry_date = updated_at + timedelta(days=30)
        else:
            expiry_date = None

        existing_subscription = Subscription.query.get(subscription_id)

        if existing_subscription:
            existing_subscription.customer_id = customer_id
            existing_subscription.customer_name = customer_name
            existing_subscription.customer_email = customer_email
            existing_subscription.product_name = product_name
            existing_subscription.plan_id = plan_id
            existing_subscription.status = status
            existing_subscription.created_at = created_at
            existing_subscription.updated_at = updated_at
            existing_subscription.expiry_date = expiry_date
        else:
            new_subscription = Subscription(
                subscription_id=subscription_id,
                customer_id=customer_id,
                customer_name=customer_name,
                customer_email=customer_email,
                product_name=product_name,
                plan_id=plan_id,
                status=status,
                created_at=created_at,
                updated_at=updated_at,
                expiry_date=expiry_date
            )
            db.session.add(new_subscription)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error committing to the database: {e}")
        return jsonify({"error": "Failed to store subscriptions in the database"})

    return jsonify({"status": "Subscriptions stored successfully"})

@app.route('/success')
def success():
    session_id = request.args.get('session_id')
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        subscription_id = session.subscription
        subscription = stripe.Subscription.retrieve(subscription_id)
        customer = stripe.Customer.retrieve(subscription.customer)
        payment_method = stripe.PaymentMethod.retrieve(subscription.default_payment_method)
        invoices = stripe.Invoice.list(subscription=subscription_id, limit=1)
        invoice = invoices.data[0] if invoices.data else None
        invoice_pdf_url = invoice.invoice_pdf if invoice else None

        customer_name = customer.name
        created_date = datetime.fromtimestamp(subscription.created).strftime('%B %d, %Y %I:%M %p')
        current_period_start = datetime.fromtimestamp(subscription.current_period_start).strftime('%B %d, %Y')
        current_period_end = datetime.fromtimestamp(subscription.current_period_end).strftime('%B %d, %Y')
        payment_method_details = f"•••• {payment_method.card.last4}"
        tax_calculation = 'No tax rate applied' if not subscription.default_tax_rates else 'Tax rate applied'

        email_body = f"""
        Success! Payment was successful.

        Customer: {customer_name}
        Created: {created_date}
        Current period: {current_period_start} to {current_period_end}
        ID: {subscription_id}
        Discounts: None
        Billing method: Charge specific payment method
        Payment method: {payment_method_details}
        Tax calculation: {tax_calculation}
        """

        if invoice:
            invoice_date = datetime.fromtimestamp(invoice.created).strftime('%B %d, %Y %I:%M %p')
            amount_due = invoice.amount_due / 100.0  
            email_body += f"\n\nInvoice Details:\nInvoice Date: {invoice_date}\nAmount Due: RS{amount_due:.2f}"

        if invoice_pdf_url:
            invoice_pdf = requests.get(invoice_pdf_url)
            invoice_filename = f"{subscription_id}_invoice.pdf"
            with open(invoice_filename, "wb") as f:
                f.write(invoice_pdf.content)

            msg = Message(subject='Subscription Created', sender=app.config['MAIL_USERNAME'], recipients=[customer.email])
            msg.body = email_body
            with app.open_resource(invoice_filename) as attachment:
                msg.attach(invoice_filename, 'application/pdf', attachment.read())

            mail.send(msg)

            os.remove(invoice_filename)
        else:
            msg = Message(subject='Subscription Created', sender=app.config['MAIL_USERNAME'], recipients=[customer.email])
            msg.body = email_body
            mail.send(msg)
            
        return 'Success! Email sent with subscription details.'
    
    except Exception as e:
        return jsonify({'error': str(e)})



@app.route('/cancel')
def cancel():
    return 'Payment was canceled.'

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = request.get_json()
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price': data['price_id'],
                    'quantity': 1,
                },
            ],
            mode='subscription',
            success_url = 'http://127.0.0.1:5000/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='http://127.0.0.1:5000/cancel',
        )
        
        checkout_url = session.url
        return jsonify({'checkout_url': checkout_url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)})

@app.route('/upgrade-subscription', methods=['POST'])
def upgrade_subscription():
    data = request.json
    customer_id = data['customer_id']
    subscription_id = data['subscription_id']
    new_plan_id = data['new_plan_id']

    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        updated_subscription = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=False,
            items=[{
                "id": subscription['items']['data'][0].id,
                "price": new_plan_id,
            }]
        )
        invoice = stripe.Invoice.list(customer=customer_id, subscription=subscription_id, limit=1)
        if invoice.data:
            invoice_url = invoice.data[0].hosted_invoice_url
            return jsonify({'invoice_url': invoice_url})
        else:
            return jsonify({'message': 'No invoice found for the subscription.'})
    
    except stripe.error.StripeError as e:
        if e.payment_intent:
            return jsonify({'payment_intent_client_secret': e.payment_intent.client_secret})
        else:
            return jsonify({'error': str(e)})

@app.route('/cancel-subscription', methods=['POST'])
def cancel_subscription():
    data = request.get_json()
    subscription_id = data.get('subscription_id')
    try:
        stripe.Subscription.delete(subscription_id)
        return jsonify({'message': 'Subscription canceled successfully.'})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True)
