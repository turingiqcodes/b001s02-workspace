-- LOCAL TESTING ONLY. A small copy of the standard pubs sample data, used by
-- tests/test_api.py against a throwaway Postgres. Never run this on the real DB.

CREATE TABLE public.publishers (pub_id char(4) PRIMARY KEY, pub_name varchar(40), city varchar(20), state char(2), country varchar(30));
CREATE TABLE public.authors (au_id varchar(11) PRIMARY KEY, au_lname varchar(40) NOT NULL, au_fname varchar(20) NOT NULL,
  phone char(12) NOT NULL, address varchar(40), city varchar(20), state char(2), zip char(5), contract integer NOT NULL);
CREATE TABLE public.titles (title_id varchar(6) PRIMARY KEY, title varchar(80) NOT NULL, type varchar(12) NOT NULL,
  pub_id char(4) REFERENCES public.publishers, price numeric(19,4), advance numeric(19,4), royalty integer,
  ytd_sales integer, notes varchar(200), pubdate date);
CREATE TABLE public.titleauthor (au_id varchar(11) REFERENCES public.authors, title_id varchar(6) REFERENCES public.titles,
  au_ord smallint, royaltyper integer, PRIMARY KEY (au_id, title_id));
CREATE TABLE public.stores (stor_id char(4) PRIMARY KEY, stor_name varchar(40), stor_address varchar(40), city varchar(20), state char(2), zip char(5));
CREATE TABLE public.sales (stor_id char(4) REFERENCES public.stores, ord_num varchar(20), ord_date date NOT NULL, qty smallint NOT NULL,
  payterms varchar(12) NOT NULL, title_id varchar(6) REFERENCES public.titles, PRIMARY KEY (stor_id, ord_num, title_id));
CREATE TABLE public.roysched (title_id varchar(6) REFERENCES public.titles, lorange integer, hirange integer, royalty integer);
CREATE TABLE public.discounts (discounttype varchar(40) NOT NULL, stor_id char(4) REFERENCES public.stores, lowqty smallint, highqty smallint, discount numeric(4,2) NOT NULL);
CREATE TABLE public.jobs (job_id smallint PRIMARY KEY, job_desc varchar(50) NOT NULL, min_lvl smallint, max_lvl smallint);
CREATE TABLE public.employee (emp_id char(9) PRIMARY KEY, fname varchar(20) NOT NULL, minit char(1), lname varchar(30) NOT NULL,
  job_id smallint REFERENCES public.jobs, job_lvl smallint, pub_id char(4) REFERENCES public.publishers, hire_date date);

INSERT INTO public.publishers VALUES
  ('0736', 'New Moon Books', 'Boston', 'MA', 'USA'),
  ('0877', 'Binnet & Hardley', 'Washington', 'DC', 'USA'),
  ('1389', 'Algodata Infosystems', 'Berkeley', 'CA', 'USA'),
  ('1622', 'Five Lakes Publishing', 'Chicago', 'IL', 'USA'),
  ('1756', 'Ramona Publishers', 'Dallas', 'TX', 'USA'),
  ('9901', 'GGG&G', 'München', '', 'Germany'),
  ('9952', 'Scootney Books', 'New York', 'NY', 'USA'),
  ('9999', 'Lucerne Publishing', 'Paris', '', 'France');

INSERT INTO public.authors VALUES
  ('172-32-1176', 'White', 'Johnson', '408 496-7223', '10932 Bigge Rd.', 'Menlo Park', 'CA', '94025', 1),
  ('213-46-8915', 'Green', 'Marjorie', '415 986-7020', '309 63rd St. #411', 'Oakland', 'CA', '94618', 1),
  ('238-95-7766', 'Carson', 'Cheryl', '415 548-7723', '589 Darwin Ln.', 'Berkeley', 'CA', '94705', 1),
  ('267-41-2394', 'O''Leary', 'Michael', '408 286-2428', '22 Cleveland Av. #14', 'San Jose', 'CA', '95128', 1),
  ('274-80-9391', 'Straight', 'Dean', '415 834-2919', '5420 College Av.', 'Oakland', 'CA', '94609', 1),
  ('341-22-1782', 'Smith', 'Meander', '913 843-0462', '10 Mississippi Dr.', 'Lawrence', 'KS', '66044', 0),
  ('409-56-7008', 'Bennet', 'Abraham', '415 658-9932', '6223 Bateman St.', 'Berkeley', 'CA', '94705', 1),
  ('427-17-2319', 'Dull', 'Ann', '415 836-7128', '3410 Blonde St.', 'Palo Alto', 'CA', '94301', 1),
  ('472-27-2349', 'Gringlesby', 'Burt', '707 938-6445', 'PO Box 792', 'Covelo', 'CA', '95428', 1),
  ('486-29-1786', 'Locksley', 'Charlene', '415 585-4620', '18 Broadway Av.', 'San Francisco', 'CA', '94130', 1),
  ('527-72-3246', 'Greene', 'Morningstar', '615 297-2723', '22 Graybar House Rd.', 'Nashville', 'TN', '37215', 0),
  ('648-92-1872', 'Blotchet-Halls', 'Reginald', '503 745-6402', '55 Hillsdale Bl.', 'Corvallis', 'OR', '97330', 1),
  ('672-71-3249', 'Yokomoto', 'Akiko', '415 935-4228', '3 Silver Ct.', 'Walnut Creek', 'CA', '94595', 1),
  ('712-45-1867', 'del Castillo', 'Innes', '615 996-8275', '2286 Cram Pl. #86', 'Ann Arbor', 'MI', '48105', 1),
  ('722-51-5454', 'DeFrance', 'Michel', '219 547-9982', '3 Balding Pl.', 'Gary', 'IN', '46403', 1),
  ('724-08-9931', 'Stringer', 'Dirk', '415 843-2991', '5420 Telegraph Av.', 'Oakland', 'CA', '94609', 0),
  ('724-80-9391', 'MacFeather', 'Stearns', '415 354-7128', '44 Upland Hts.', 'Oakland', 'CA', '94612', 1),
  ('756-30-7391', 'Karsen', 'Livia', '415 534-9219', '5720 McAuley St.', 'Oakland', 'CA', '94609', 1),
  ('807-91-6654', 'Panteley', 'Sylvia', '301 946-8853', '1956 Arlington Pl.', 'Rockville', 'MD', '20853', 1),
  ('846-92-7186', 'Hunter', 'Sheryl', '415 836-7128', '3410 Blonde St.', 'Palo Alto', 'CA', '94301', 1),
  ('893-72-1158', 'McBadden', 'Heather', '707 448-4982', '301 Putnam', 'Vacaville', 'CA', '95688', 0),
  ('899-46-2035', 'Ringer', 'Anne', '801 826-0752', '67 Seventh Av.', 'Salt Lake City', 'UT', '84152', 1),
  ('998-72-3567', 'Ringer', 'Albert', '801 826-0752', '67 Seventh Av.', 'Salt Lake City', 'UT', '84152', 1);

INSERT INTO public.titles VALUES
  ('BU1032', 'The Busy Executive''s Database Guide', 'business', '1389', 19.99, NULL, NULL, NULL, NULL, '1991-06-12'),
  ('BU1111', 'Cooking with Computers', 'business', '1389', 11.95, NULL, NULL, NULL, NULL, '1991-06-09'),
  ('BU2075', 'You Can Combat Computer Stress!', 'business', '0736', 2.99, NULL, NULL, NULL, NULL, '1991-06-30'),
  ('BU7832', 'Straight Talk About Computers', 'business', '1389', 19.99, NULL, NULL, NULL, NULL, '1991-06-22'),
  ('MC2222', 'Silicon Valley Gastronomic Treats', 'mod_cook', '0877', 19.99, NULL, NULL, NULL, NULL, '1991-06-09'),
  ('MC3021', 'The Gourmet Microwave', 'mod_cook', '0877', 2.99, NULL, NULL, NULL, NULL, '1991-06-18'),
  ('MC3026', 'The Psychology of Computer Cooking', 'UNDECIDED', '0877', NULL, NULL, NULL, NULL, NULL, NULL),
  ('PC1035', 'But Is It User Friendly?', 'popular_comp', '1389', 22.95, NULL, NULL, NULL, NULL, '1991-06-30'),
  ('PC8888', 'Secrets of Silicon Valley', 'popular_comp', '1389', 20.0, NULL, NULL, NULL, NULL, '1994-06-12'),
  ('PC9999', 'Net Etiquette', 'popular_comp', '1389', NULL, NULL, NULL, NULL, NULL, NULL),
  ('PS1372', 'Computer Phobic and Non-Phobic Individuals', 'psychology', '0877', 21.59, NULL, NULL, NULL, NULL, '1991-10-21'),
  ('PS2091', 'Is Anger the Enemy?', 'psychology', '0736', 10.95, NULL, NULL, NULL, NULL, '1991-06-15'),
  ('PS2106', 'Life Without Fear', 'psychology', '0736', 7.0, NULL, NULL, NULL, NULL, '1991-10-05'),
  ('PS3333', 'Prolonged Data Deprivation', 'psychology', '0736', 19.99, NULL, NULL, NULL, NULL, '1991-06-12'),
  ('PS7777', 'Emotional Security: A New Algorithm', 'psychology', '0736', 7.99, NULL, NULL, NULL, NULL, '1991-06-12'),
  ('TC3218', 'Onions, Leeks, and Garlic', 'trad_cook', '0877', 20.95, NULL, NULL, NULL, NULL, '1991-10-21'),
  ('TC4203', 'Fifty Years in Buckingham Palace Kitchens', 'trad_cook', '0877', 11.95, NULL, NULL, NULL, NULL, '1991-06-12'),
  ('TC7777', 'Sushi, Anyone?', 'trad_cook', '0877', 14.99, NULL, NULL, NULL, NULL, '1991-06-12');

INSERT INTO public.titleauthor VALUES
  ('409-56-7008', 'BU1032', 1, 100),
  ('486-29-1786', 'PS7777', 1, 100),
  ('486-29-1786', 'PC9999', 1, 100),
  ('712-45-1867', 'MC2222', 1, 100),
  ('172-32-1176', 'PS3333', 1, 100),
  ('213-46-8915', 'BU1032', 1, 100),
  ('238-95-7766', 'PC1035', 1, 100),
  ('213-46-8915', 'BU2075', 1, 100),
  ('998-72-3567', 'PS2091', 1, 100),
  ('899-46-2035', 'PS2091', 1, 100),
  ('998-72-3567', 'PS2106', 1, 100),
  ('722-51-5454', 'MC3021', 1, 100),
  ('899-46-2035', 'MC3021', 1, 100),
  ('807-91-6654', 'TC3218', 1, 100),
  ('274-80-9391', 'BU7832', 1, 100),
  ('427-17-2319', 'PC8888', 1, 100),
  ('846-92-7186', 'PC8888', 1, 100),
  ('756-30-7391', 'PS1372', 1, 100),
  ('724-80-9391', 'PS1372', 1, 100),
  ('724-80-9391', 'BU1111', 1, 100),
  ('267-41-2394', 'BU1111', 1, 100),
  ('672-71-3249', 'TC7777', 1, 100),
  ('267-41-2394', 'TC7777', 1, 100),
  ('472-27-2349', 'TC7777', 1, 100),
  ('648-92-1872', 'TC4203', 1, 100);

INSERT INTO public.stores VALUES
  ('6380', 'Eric the Read Books', '788 Catamaugus Ave.', 'Seattle', 'WA', '98056'),
  ('7066', 'Barnum''s', '567 Pasadena Ave.', 'Tustin', 'CA', '92789'),
  ('7067', 'News & Brews', '577 First St.', 'Los Gatos', 'CA', '96745'),
  ('7131', 'Doc-U-Mat: Quality Laundry and Books', '24-A Avogadro Way', 'Remulade', 'WA', '98014'),
  ('7896', 'Fricative Bookshop', '89 Madison St.', 'Fremont', 'CA', '90019'),
  ('8042', 'Bookbeat', '679 Carson St.', 'Portland', 'OR', '89076');

INSERT INTO public.sales VALUES
  ('6380', '6871', '1994-09-14', 5, 'Net 60', 'BU1032'),
  ('6380', '722a', '1994-09-13', 3, 'Net 60', 'PS2091'),
  ('7066', 'A2976', '1993-05-24', 50, 'Net 30', 'PC8888'),
  ('7066', 'QA7442.3', '1994-09-13', 75, 'ON invoice', 'PS2091'),
  ('7067', 'D4482', '1994-09-14', 10, 'Net 60', 'PS2091'),
  ('7067', 'P2121', '1992-06-15', 40, 'Net 30', 'TC3218'),
  ('7067', 'P2121', '1992-06-15', 20, 'Net 30', 'TC4203'),
  ('7067', 'P2121', '1992-06-15', 20, 'Net 30', 'TC7777'),
  ('7131', 'N914008', '1994-09-14', 20, 'Net 30', 'PS2091'),
  ('7131', 'N914014', '1994-09-14', 25, 'Net 30', 'MC3021'),
  ('7131', 'P3087a', '1993-05-29', 20, 'Net 60', 'PS1372'),
  ('7131', 'P3087a', '1993-05-29', 25, 'Net 60', 'PS2106'),
  ('7131', 'P3087a', '1993-05-29', 15, 'Net 60', 'PS3333'),
  ('7131', 'P3087a', '1993-05-29', 25, 'Net 60', 'PS7777'),
  ('7896', 'QQ2299', '1993-10-28', 15, 'Net 60', 'BU7832'),
  ('7896', 'TQ456', '1993-12-12', 10, 'Net 60', 'MC2222'),
  ('7896', 'X999', '1993-02-21', 35, 'ON invoice', 'BU2075'),
  ('8042', '423LL922', '1994-09-14', 15, 'ON invoice', 'MC3021'),
  ('8042', '423LL930', '1994-09-14', 10, 'ON invoice', 'BU1032'),
  ('8042', 'P723', '1993-03-11', 25, 'Net 30', 'BU1111'),
  ('8042', 'QA879.1', '1993-05-22', 30, 'Net 30', 'PC1035');

INSERT INTO public.roysched VALUES
  ('BU1032', 0, 5000, 10),
  ('MC3026', 0, 2000, 10);

INSERT INTO public.discounts VALUES
  ('Initial Customer', NULL, NULL, NULL, 10.5),
  ('Volume Discount', NULL, 100, 1000, 6.7),
  ('Customer Discount', '8042', NULL, NULL, 5.0);

INSERT INTO public.jobs VALUES
  (2, 'Chief Executive Officer', 10, 250),
  (3, 'Business Operations Manager', 10, 250),
  (4, 'Chief Financial Officer', 10, 250),
  (5, 'Publisher', 10, 250),
  (6, 'Managing Editor', 10, 250),
  (7, 'Marketing Manager', 10, 250),
  (8, 'Public Relations Manager', 10, 250),
  (9, 'Acquisitions Manager', 10, 250),
  (10, 'Productions Manager', 10, 250),
  (12, 'Editor', 10, 250),
  (13, 'Sales Representative', 10, 250),
  (14, 'Designer', 10, 250);

INSERT INTO public.employee VALUES
  ('PMA42628M', 'Paolo', NULL, 'Accorti', 13, 35, '0877', '1992-08-27'),
  ('PSA89086M', 'Pedro', NULL, 'Afonso', 14, 89, '1389', '1990-12-24'),
  ('VPA30890F', 'Victoria', NULL, 'Ashworth', 6, 140, '0877', '1990-09-13'),
  ('H-B39728F', 'Helen', NULL, 'Bennett', 12, 35, '0877', '1989-09-21'),
  ('L-B31947F', 'Lesley', NULL, 'Brown', 7, 120, '0877', '1991-02-13'),
  ('F-C16315M', 'Francisco', NULL, 'Chang', 4, 227, '9952', '1990-11-03'),
  ('PTC11962M', 'Philip', NULL, 'Cramer', 2, 215, '9952', '1989-11-11'),
  ('A-C71970F', 'Aria', NULL, 'Cruz', 10, 87, '1389', '1991-10-26'),
  ('AMD15433F', 'Ann', NULL, 'Devon', 3, 200, '9952', '1991-07-16'),
  ('ARD36773F', 'Anabela', NULL, 'Domingues', 8, 100, '0877', '1993-01-27'),
  ('PHF38899M', 'Peter', NULL, 'Franken', 10, 75, '0877', '1992-05-17'),
  ('PXH22250M', 'Paul', NULL, 'Henriot', 5, 159, '0877', '1993-08-19'),
  ('CFH28514M', 'Carlos', NULL, 'Hernadez', 5, 211, '9999', '1989-04-21'),
  ('PDI47470M', 'Palle', NULL, 'Ibsen', 7, 195, '0736', '1993-05-09');

-- The real BookNest DB has contract as boolean; the rows above use the classic 0/1.
ALTER TABLE public.authors ALTER COLUMN contract TYPE boolean USING contract <> 0;

-- The real BookNest DB's CHECK constraints, so a validation gap fails here before it fails there.
ALTER TABLE public.authors ADD CHECK (au_id ~ '^[0-9]{3}-[0-9]{2}-[0-9]{4}$'), ADD CHECK (zip ~ '^[0-9]{5}$');
ALTER TABLE public.titles ADD CHECK (price >= 0), ADD CHECK (advance >= 0), ADD CHECK (royalty BETWEEN 0 AND 100),
  ADD CHECK (ytd_sales >= 0);
ALTER TABLE public.titleauthor ADD CHECK (au_ord > 0), ADD CHECK (royaltyper BETWEEN 0 AND 100);
ALTER TABLE public.stores ADD CHECK (zip ~ '^[0-9]{5}$');
ALTER TABLE public.sales ADD CHECK (qty > 0);
ALTER TABLE public.discounts ADD CHECK (lowqty >= 0), ADD CHECK (discount >= 0),
  ADD CHECK (highqty IS NULL OR lowqty IS NULL OR highqty >= lowqty);
ALTER TABLE public.roysched ADD CHECK (lorange >= 0), ADD CHECK (royalty BETWEEN 0 AND 100),
  ADD CHECK (hirange IS NULL OR lorange IS NULL OR hirange >= lorange);
ALTER TABLE public.jobs ADD CHECK (min_lvl >= 10), ADD CHECK (max_lvl <= 250), ADD CHECK (max_lvl >= min_lvl);
-- NOT VALID: the classic rows above include IDs like H-B39728F; new and edited rows are still checked.
ALTER TABLE public.employee ADD CONSTRAINT employee_emp_id_check CHECK (emp_id ~ '^[A-Za-z]{3}[0-9]{5}[A-Za-z]$') NOT VALID;
