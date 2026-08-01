import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import chartink_utils

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "811302410")
PORT = 5000

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            print(f"\n[+] Received Webhook: {data}")
            
            ticker = data.get("ticker", "UNKNOWN").strip().upper()
            price = data.get("price", "0")
            signal = data.get("signal", "BUY").strip().upper()
            
            # Format price as float if possible
            try:
                price_val = float(price)
            except ValueError:
                price_val = 0.0
                
            # Optional zones from TradingView plots
            resistance_1 = data.get("resistance_1", "-")
            resistance_2 = data.get("resistance_2", "-")
            support_1 = data.get("support_1", "-")
            
            # 1. Update strategy_performance.csv
            stocks = [{"nsecode": ticker, "close": price_val}]
            chartink_utils.track_performance(f"Elephant Edge {signal}", stocks)
            
            # 2. Format Telegram message
            message_lines = [
                f"🐘 | Elephant Edge Alert",
                "",
                f"Stock: {ticker}",
                f"Signal: {signal}",
                f"Trigger Price: {price}",
                ""
            ]
            if resistance_1 != "-":
                message_lines.append(f"Resistance 1: {resistance_1}")
            if resistance_2 != "-":
                message_lines.append(f"Resistance 2: {resistance_2}")
            if support_1 != "-":
                message_lines.append(f"Support 1: {support_1}")
                
            message = "\n".join(message_lines)
            
            # 3. Send Telegram Alert
            chartink_utils.send_telegram_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
            
            # Send HTTP success response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            print("[+] Webhook processed and alert sent successfully.")
            
        except Exception as e:
            print(f"[-] Error handling webhook: {e}")
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

def run(server_class=HTTPServer, handler_class=WebhookHandler):
    server_address = ('', PORT)
    httpd = server_class(server_address, handler_class)
    print(f"[*] Starting webhook receiver server on port {PORT}...")
    print(f"[*] Waiting for TradingView signals...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Stopping server...")
        httpd.server_close()

if __name__ == "__main__":
    run()
