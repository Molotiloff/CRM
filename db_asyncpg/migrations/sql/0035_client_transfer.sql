ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_deal_type_check;
ALTER TABLE deals ADD CONSTRAINT deals_deal_type_check CHECK (deal_type IN (
    'sale', 'purchase', 'deposit', 'withdrawal', 'delivery', 'transfer_city',
    'conversion', 'yuan', 'invoice', 'profit', 'best_change', 'client_transfer'
));
