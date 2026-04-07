Use this flow against the Kubernetes deployment via ingress.

Seller flow:

Create account and login:
```bash
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 create-account --name "Alice Store" --login alice --password secret
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 login --login alice --password secret
```

Take the returned `session_id` and export it:
```bash
export SESS_SELLER='<paste_session_id_here>'
```

Register an item for sale:
```bash
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 --session "$SESS_SELLER" register-item \
  --name "iPhone 15" --category 1 --keywords phone apple ios --condition new --price 999.99 --quantity 3
```

Save the returned item tuple parts:
```bash
export ITEM_CAT=1
export ITEM_ID='<paste_item_id_integer_here>'
```

List seller's items for sale:
```bash
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 --session "$SESS_SELLER" display-items
```

Change price + update units using the tuple parts:
```bash
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 --session "$SESS_SELLER" change-price \
  --category "$ITEM_CAT" --item "$ITEM_ID" --price 899.99

python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 --session "$SESS_SELLER" update-units \
  --category "$ITEM_CAT" --item "$ITEM_ID" --quantity 5
```


Buyer flow:

Create account and login:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 create-account --name "Bob" --login bob --password secret
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 login --login bob --password secret
```

Export buyer session:
```bash
export SESS_BUYER='<paste_session_id_here>'
```

Search in the same category:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" search --category "$ITEM_CAT" --keywords phone apple
```

Get item using tuple:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" get-item \
  --category "$ITEM_CAT" --item "$ITEM_ID"
```

Add to cart, view cart, remove from cart:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" add-to-cart \
  --category "$ITEM_CAT" --item "$ITEM_ID" --quantity 2

python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" cart

python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" remove-from-cart \
  --category "$ITEM_CAT" --item "$ITEM_ID" --quantity 1

python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" cart
```

Provide feedback:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" feedback \
  --category "$ITEM_CAT" --item "$ITEM_ID" --vote up
```

Check seller rating from buyer side:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" seller-rating --seller 1
```

Make purchase (processes whole cart):
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" make-purchase \
  --name "Bob" --card-number 4111111111111111 --expiry 12/30 --security-code 123
```

On success, cart should be cleared and purchase history updated. If the transaction is declined (10% chance), rerun the same command to retry.

Check purchases:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" purchases
```

Log out:
```bash
python clients/buyers/cli.py --host marketplace-buyers.richardr.dev --port 80 --session "$SESS_BUYER" logout
python clients/sellers/cli.py --host marketplace-sellers.richardr.dev --port 80 --session "$SESS_SELLER" logout
```
