#! /usr/bin/python3
#
"""Report data to a SQL database
-----------------------------

(for the time being, restricted to MariaDB, since we use the MariaDB
connector)

Reports execution data into a set of SQL tables in a database.

"""
import functools
import os
import threading

import logging
import mariadb

import commonl
import tcfl.tc

class driver_summary(tcfl.tc.report_driver_c):
    """Report a summary of test case execution to an SQL database

    Summaries report only aggregated data and not detailed testcase
    execution data. Read on.

    :param str hostname: *USER:PASSWORD@HOSTNAME*

      - *USER:PASSWORD* has to have schema powers to create tables
         and modify columns.

         *PASSWORD* can be given as described
         in :func:`commonl.split_user_pwd_hostname` to obtain the
         password from keyrings or other locations.

    :param str database: name in the datase in the given host where
       data is to be stored.

    :param str password: (optional) password to use to connect,
       overriding whatever is set in *hostname*. Note it will be
       passed through :func:`commonl.password_get`, thus it can use
       the *FILE* and *KEYRING* metdhos to describe.

    :param int port: (optional; default 3307) port where the database
      server is listening on.

    :param bool ssl: (optional; default *True*) use SSL or not.

    :param dict mariadb_extra_opts: (optional) extra options to the
      MadriaDB connection initialization
      (:meth:`mariadb.connect`). This takes the form of a dictionary
      keyed by string (valid Python identifiers):

      >>> mariadb_extra_opts = {
      >>>     "read_timeout": 3,
      >>>     "ssl_cert": "/path/to/SSL.crt"
      >>> }

    :param str table_name_prefix: (optional; default none) Name to
      prefix the created tables with.

      This is usually set to be the name of a pipeline which is
      running testcases on logically grouped hardware (eg: all
      testcases running performance tests on HW type A), so that the
      tables reported can be differentiated by the hardware types.

    Design
    ^^^^^^

    Entry point is :meth:`report`, which is called by the reporting
    API when the testcase reports any message or data.

    The driver will accumulate all the data in memory until a
    *COMPLETION* message is received, marking the ned of the testcase
    execution and then it will flush it to the database. This is done
    like that to avoid it causing impact in testcase timing due to
    possible networking issues.

    The reporting will take data from the testcase execution and put it in
    different tables in the SQL database.

    - tables will be prefixed with the configured *table_name_prefix*
      and *prefix_bare* (if given).

    - tables will have a primary key corresponding to the *Run
      ID* given to *tcf run* with *-i* or *--runid*. If no *RunID* has
      been given, *no RunID* is chosen.

      This means that data coming from testcases executed with a RunID
      may override data from the same testcase exectued in the past
      with the same RunID.

    - columns will be added dynamically when not present.

    Note any table name or column which is longer than 64 chars will
    be encoded as *fieldid:10CHARHASH* -- this is because SQL can't do
    anything longer than that.

    In case of the table name, there is the limitation on putting
    together *TABLE_NAME_PREFIX PREFIX_BARE [NAME]*--if
    *TABLE_NAME_PREFIX PREFIX_BARE* is longer than 45 chars, we'll
    still break the limit (FIXME: assert this)

    Currently, the following tables will be created:

    - Summary: contains summaries about how many testcases where
      executed and how many passed, failed, errored, blocked or
      skipped

      Data from more recent testcases with same RunIDs accumulate from
      previous execution using the same RunID.

    - History: a column for each executed testcase and their result on
      each run. This will be a letter (see :data:`tcfl.tc.valid_results`):

      - *P*: Passed
      - *F*: Failed
      - *E*: Errored
      - *B*: Blocked
      - *S*: Skipped

      Data from more recent testcases with same RunIDs overwrite
      previous execution using the same RunID

    - data reported with :meth:`target.report_data
      <tcfl.tc.reporter_c.report_data>` or :meth:`self.report_data
      <tcfl.tc.reporter_c.report_data>`: these will be put in a table
      named after the data domain, with the data name on each column.

      Data from more recent testcases with same RunIDs overwrite
      previous execution using the same RunID

      Note data is not segregated by target; it is up to the execution
      pipeline and testcases to either define proper domains so
      reports don't collide or are overriden.

    Tables and columns will be created dynamically when they do not
    exist, since much of the columns details come from the testcases
    and it can't be known ahead of time what they will be.

    Assumptions
    ^^^^^^^^^^^

    - Python 3 dictionaries are ordered by insertion order (true as of
      v3.6+)

    - RunIDs fit in the length of a column identifier

    - SQL injection! All `{FIELD}` references (between backticks) are
      not protected, so all the calls that feed stuff like table names
      and column names HAVE to have been passed by
      _id_maybe_encode_cache(), which will encode backticks away (to
      avoid SQL injections) and also replace too long names with a
      hashed field ID.

      Note the table names we do it in the call sites, so it doesn't
      have to be done on each SQL statement expansion.

    System setup / Requirements
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^

    Install the MariaDB connector::

      $ pip3 install --user mariadb

    PENDING
    ^^^^^^^

    - Create more config options that allows to give more info about data
      types or defaults in class specific fashion (eg: to give the history
      table a varchar(255)); suggest default_type to complement
      index_column, index_value.

    """
    def __init__(self, hostname, database, password = None,
                 port = 3307, ssl = True,
                 table_name_prefix = "", mariadb_extra_opts = None):
        assert isinstance(hostname, str)
        assert isinstance(database, str)
        assert password == None or isinstance(password, str)
        assert isinstance(port, int)
        assert isinstance(ssl, bool)
        assert isinstance(table_name_prefix, str)
        if mariadb_extra_opts:
            commonl.assert_dict_key_strings(mariadb_extra_opts, "mariadb_extra_opts")
            self.mariadb_extra_opts = mariadb_extra_opts
        else:
            self.mariadb_extra_opts = {}

        self.user, self.password, self.host = \
            commonl.split_user_pwd_hostname(hostname)
        if password:
            self.password = commonl.password_get(self.host,
                                                 self.user, password)
        self.port = port
        self.database = database
        self.ssl = ssl
        self.table_name_prefix_raw = table_name_prefix
        self.table_name_prefix = self._sql_id_esc(table_name_prefix)
        self.docs = {}
        tcfl.tc.report_driver_c.__init__(self)


    @staticmethod
    def _sql_id_esc(key):
        # we'll enclose every table/column identifier in backticks, so
        # we have to encode them in the value to avoid injection
        # attacks
        return key.replace("`", "``")


    #: Map of Python types to SQL types
    #:
    #: Used when auto-creating columns, which kinda limits what kind
    #: of data we can feed to _table_row_*()
    sql_type_by_python_type = {
        bytes: "binary",
        bool: "boolean",
        #datetime: "datetime",
        float: "float",
        str: "text",	# I guess limited... to 65k in MariaDB
        float: "double",
        int: "int",
        bytes: "varbinary",
    }

    defaults_map = {
        "Blocked": 0,
        "Errored": 0,
        "Failed": 0,
        "Passed": 0,
        "RunID": "no RunID",
        "RunID-TestcaseName": "NULL",
        # key length if fixed to 10 in _id_maybe_encode_cache()
        "FieldID": "NULL",
        # because we use this as primary key for the History tables
        "Testcase name": "no testcase",
        "Skipped": 0,
        "Total Count": 0,
    }

    sql_type_by_field = {
        "Blocked": "int",
        "Errored": "int",
        "Failed": "int",
        "Passed": "int",
        # because we use this as primary key for most tables
        "RunID": "varchar(255)",
        # there is the posibility that we won't fit RunID+TestcaseName
        # in this limit...
        "RunID-TestcaseName": "varchar(1024)",
        # key length if fixed to 10 in _id_maybe_encode_cache()
        "FieldID": "varchar(11)",
        # because we use this as primary key for the History tables
        "Testcase name": "varchar(255)",
        "Skipped": "int",
        "Total Count": "int",
    }


    def sql_type(self, field, value):
        sql_type = self.sql_type_by_field.get(field, None)
        if not sql_type:
            sql_type = self.sql_type_by_python_type.get(type(value), None)
        return sql_type


    @functools.lru_cache(maxsize = 200)
    def _connection_get_cache(self, _tls, _made_in_pid):
        # we don't use _tls and _made_in_pid; they are just there for
        # functools.lru_cache to do its caching magic and have them
        # pinned to a thread or a PID (based on if this is being
        # executed in a thread pool or in a process pool).
        connection = mariadb.connect(
            user = self.user, password = self.password,
            host = self.host, port = self.port,
            database = self.database,
            ssl = self.ssl,
            **self.mariadb_extra_opts)
        connection.auto_reconnect = True
        return connection


    def _connection_get(self):
        # Return a connection to the databse
        #
        # We cache them by process and thread, so we always reuse them
        # (since they are costly) and only have one of each.
        tls = threading.get_ident()
        connection = self._connection_get_cache(tls, os.getpid())
        return connection


    def _table_create(self, cursor, table_name,
                      defaults = False, index_column = None,
                      **fields):
        # Create a table with the given fields
        #
        # Maybe set the field's values as defaults.
        #
        # Set as primary key the index_column (if given); this will
        # normally be the RunID and there is a limitation in that it
        # can't be an unlimited length SQL type; hence we set it in
        # self.sql_types_by_field to be a vachar(255) (if anyone sets
        # a RunID longer than 255, ... their problem. FIXME: needs
        # runtime verification)
        #
        # The SQL command is basically
        #
        #   create table TABLENAME (
        #     FIELD1 TYPE1 [default DEFAULT1],
        #     FIELD2 TYPE2 [default DEFAULT2],
        #     ...,
        #     [primary key ( FIELDx );
        #
        if defaults:
            cmd = f"create table if not exists `{table_name}` ( " \
                + ", ".join(
                    f"`{field}` {self.sql_type(field, value)} default ?"
                    for field, value in fields.items()
                ) \
                + ( f", primary key (`{index_column}`)" if index_column else "" ) \
                + " );"
            values = tuple(self.defaults_map.get(column, None)
                           for column in fields)
            cursor.execute(cmd, values)
        else:
            cmd = f"create table if not exists `{table_name}` ( " \
                + ", ".join(
                    f"`{field}` {self.sql_type(field, value)}"
                    for field, value in fields.items()
                ) \
                + (
                    f", primary key (`{index_column}`)"
                    if index_column else ""
                ) \
                + " );"
            cursor.execute(cmd)


    def _table_columns_update(self, cursor, table_name,
                              defaults = False,  **fields):
        # Add missing columns to a table, maybe setting defaults
        #

        # First list the current columns
        cmd = \
            f"select column_name" \
            f" from information_schema.columns" \
            f" where table_name = '{table_name}'"
        cursor.execute(cmd)
        columns = set(row[0] for row in cursor)

        fields_wanted = fields.keys()
        columns_missing = fields_wanted - columns
        if not columns_missing:
            return

        # add new missing new columns
        if defaults:
            cmd = \
                f"alter table `{table_name}` add ( " \
                + f", ".join(
                    f" `{column}` {self.sql_type(column, fields[column])}"
                    f" default {self.defaults_map.get(column, None)} "
                    for column in columns_missing
                ) + f" );"
        else:
            cmd = \
                f"alter table `{table_name}` add ( " \
                + f", ".join(
                    f" `{column}` {self.sql_type(column, fields[column])}"
                    for column in columns_missing
                ) + " );"
        cursor.execute(cmd)


    def _table_row_insert(self, cursor, table_name, **fields):
        # insert all the values in the specific columns
        #
        ## insert into TABLENAME ( FIELD1, FIELD2...) data ( VALUE1, VALUE2...)
        #
        # note we use %s placeholders for the values, to let the
        # python itnerface type them properly and pass execute() a
        # tuple with the values
        cmd = \
            f"insert into `{table_name}` ( " \
            " `" + "`, `".join(fields.keys()) + "` )" \
            " values ( " + " , ".join("?" for _ in fields.values()) + " );"
        cursor.execute(cmd, tuple(fields.values()))


    @functools.lru_cache(maxsize = 2048)
    def _id_maybe_encode_cache(self, _tls, _made_in_pid, identifier, max_len):
        """
        If an identifier is longer than the maximum, convert it and
        register it.

        Register it in the *Field IDs* table so we can later refer to
        it as needed.

        :param str identifier: identifier to check and maybe convert

        :return str: identifier if (shorter than :data:`id_max_len`)
          or the encoded name if it was longer.
        """
        if len(identifier) >= max_len:
            fieldid = commonl.mkid(identifier, 10)
            self.table_row_update("Field IDs", "FieldID", fieldid,
                                  **{ "Field Name": identifier })
            return "fieldid:" + fieldid
        return self._sql_id_esc(identifier)


    def _id_maybe_encode(self, identifier, max_len = 32):
        return self._id_maybe_encode_cache(
            threading.get_ident(), os.getpid(),
            identifier, max_len)


    def _table_name_prepare(self, table_name, prefix_bare):
        prefix_esc = self.table_name_prefix + self._sql_id_esc(prefix_bare)
        prefix_len = len(self.table_name_prefix_raw) + len(prefix_bare)
        _table_name = prefix_esc + self._id_maybe_encode(table_name, 64 - prefix_len)
        return _table_name.strip()	# table names can't start/end w space


    def table_row_update(self, table_name, index_column, index_value,
                         prefix_bare = "", **fields):
        # insert/update fields in a table
        #
        # Use the index value of the index column to find the row to
        # update or insert a new one if not present.
        #
        # If the table does not exist, create it; if any column is
        # missing, add them.
        _table_name = self._table_name_prepare(table_name, prefix_bare)

        connection = self._connection_get()
        with connection.cursor() as cursor:

            while True:
                # Now try to insert the row
                #
                # - if the primary key is duplicated, update the
                #   values
                #
                #   Seriously this SQL thing...
                #
                #   insert into TABLENAME (
                #       INDEX_COLUMN, FIELD1,  FIELD2 ...)
                #   values ( INDEX_VALUE, VALUE1, VALUE2 ...  )
                #   on duplicate key update
                #      FIELD1 = value(FIELD1),
                #      FIELD2 = value(FIELD2),
                #      ...;
                #
                #   If there is no row with INDEX_COLUMN with
                #   INDEX_VALUE, insert it with those FIELDs,
                #   otherwise, update it. Clear as mud--especially the
                #   code.
                #
                #   Thanks https://stackoverflow.com/a/41894298
                #
                # - if we get errors because the table or columns
                #   still do not exist, fix'em and
                #   try again
                try:
                    cmd = \
                        f"insert into `{_table_name}` (`{index_column}`, " \
                        + ", ".join(
                            f"`{self._sql_id_esc(column)}`"
                            for column in fields
                        ) \
                        + " ) values ( ?, " + ", ".join(
                            "?"
                            for column in fields
                        ) + " ) on duplicate key update " \
                        + ", ".join(
                            f"`{self._sql_id_esc(column)}` = values(`{self._sql_id_esc(column)}`)"
                            for column in fields
                        ) + ";"
                    values = ( index_value, ) + tuple(fields.values())
                    cursor.execute(cmd, values)
                    # In theory python MariaDB does autocommit, but I
                    # guess not?
                    connection.commit()
                    break	# success, get out of the retry loop

                except mariadb.ProgrammingError as e:
                    # if the database doesn't exist, the error will read
                    #
                    ## mariadb.ProgrammingError: Table 'DBNAME.TABLENAME table' doesn't exist
                    #
                    # if there is a better way, I am all ears
                    if not str(e).endswith(f"Table '{self.database}.{_table_name}' doesn't exist"):
                        raise
                    # ops, the table does not exist, create it with
                    # these fields; guess the types from the field
                    # values and retry; but we want to have FIRST the
                    # index column -- we rely on python3 keeping
                    # insertion order for dictionaries
                    f = { index_column: index_value }
                    f.update(fields)
                    try:
                        self._table_create(cursor, _table_name,
                                           defaults = True,
                                           index_column = index_column,
                                           **f)
                        connection.commit()
                    except mariadb.OperationalError as e:
                        if str(e).endswith(f"Table '{_table_name}' already exists"):
                            # someone did it already, retry
                            continue
                        raise
                    continue	# now try to insert/update again

                except mariadb.OperationalError as e:
                    # If any column does not exist, we'll get
                    #
                    ## mariadb.OperationalError: Unknown column ...blablah
                    #
                    # if there is a better way, I am all ears
                    if not str(e).startswith("Unknown column"):
                        raise
                    self._table_columns_update(cursor, _table_name, **fields)
                    f = { index_column: index_value }
                    f.update(fields)
                    continue


    def table_row_inc(self, table_name, index_column, index_value,
                      prefix_bare = "", **fields):
        # Increment by one the listed fileds in the row matching
        # index_value
        #
        # If the row does not exist, add it with the given fields set
        # to one.
        _table_name = self._table_name_prepare(table_name, prefix_bare)

        connection = self._connection_get()
        with connection.cursor() as cursor:

            while True:
                # Now try to inser the row; if we get errors because
                # the table or columns still do not exist, fix'em and
                # try again
                try:
                    f = list(fields.keys())
                    f.remove(index_column) # no need to increase this field
                    # increase by one values of the specified columns in the row
                    # whose primary key (index_column) has the given index_value
                    #
                    ## insert into TABLENAME (INDEX_COLUMN, FIELD1, FIELD2...)
                    ## values (INDEX_VALUE, 1, 1, ...)
                    ## on duplicate key update
                    ##   FIELD1 = value(FIELD1) + 1,
                    ##   FIELD2 = value(FIELD2) + 1,
                    ##   ...;
                    #
                    # note we use %s placeholders for the values, to let the
                    # python itnerface type them properly and pass execute() a
                    # tuple with the values
                    cmd = \
                        f"insert into `{_table_name}` (`{index_column}`, " \
                        + ", ".join(f"`{column}`" for column in f) \
                        + " ) values ( ?" + ", 1" * len(f) \
                        + " ) on duplicate key update " \
                        + ", ".join(
                            f"`{column}` = `{column}` + 1"
                            for column in f
                        ) + ";"
                    cursor.execute(cmd, (index_value, ))

                    # In theory python MariaDB does autocommit, but I
                    # guess not?
                    connection.commit()
                    break	# success, get out of the retry loop

                except mariadb.ProgrammingError as e:
                    # if the database doesn't exist, the error will read
                    #
                    ## mariadb.ProgrammingError: Table 'DBNAME.TABLENAME table' doesn't exist
                    #
                    # if there is a better way, I am all ears
                    if not str(e).endswith(f"Table '{self.database}.{_table_name}' doesn't exist"):
                        raise
                    # ops, the table does not exist, create it with
                    # these fields; guess the types from the field
                    # values and retry
                    try:
                        self._table_create(cursor, _table_name,
                                           index_column = index_column,
                                           **fields)
                        connection.commit()
                    except mariadb.OperationalError as e:
                        if str(e).endswith(f"Table '{_table_name}' already exists"):
                            # someone did it already, retry
                            continue
                        raise
                    # now insert the row, we can't increase because we
                    # know there was nothing -- FIXME: what about if
                    # someone tried before us?
                    try:
                        self._table_row_insert(cursor, _table_name, **fields)
                        connection.commit()
                    except mariadb.OperationalError as e:
                        # see next, it is the same
                        if not str(e).startswith("Unknown column"):
                            raise
                        continue	# just retry and let if fail
                    # note we break vs continue, because we already inserted
                    break

                except mariadb.OperationalError as e:
                    # If any column does not exist, we'll get
                    #
                    ## mariadb.OperationalError: Unknown column ...blablah
                    #
                    # if there is a better way, I am all ears
                    if not str(e).startswith("Unknown column"):
                        raise
                    # note we set defauls to True; this will take
                    # the defaults based on the field name from
                    # defaults_map defined above.
                    self._table_columns_update(cursor, _table_name,
                                               defaults = True, **fields)
                    connection.commit()
                    continue


    def report(self, reporter, tag, ts, delta,
               level, message,
               alevel, attachments):
        # Entry point for the reporting driver from the reporting API
        #
        # We filter the messages we report, since we only do
        # summaries--thus we skip anything we are not interested on,
        # then we collect data and on COMPLETION (end of test case),
        # upload data to the database.

        # We only do summaries, so skip anything that is not reporting
        # data or testcase completions
        if tag != "DATA" and not message.startswith("COMPLETION"):
            return
        # skip global reporter, not meant to be used here
        if reporter == tcfl.tc.tc_global:
            return

        runid = reporter.kws.get('runid', "no RunID")
        hashid = reporter.kws.get('tc_hash', None)
        if not hashid:	            # can't do much if we don't have this
            return
        if not runid:
            runid = "no RunID"

        # Extract the target name where this message came from (if the
        # reporter is a target)
        if isinstance(reporter, tcfl.tc.target_c):
            tc_name = reporter.testcase.name
            target = reporter
        elif isinstance(reporter, tcfl.tc.tc_c):
            tc_name = reporter.name
            target = None
        else:
            raise AssertionError(
                "reporter is not tcfl.tc.{tc,target}_c but %s" % type(reporter))

        doc = self.docs.setdefault((runid, hashid, tc_name),
                                   dict(data = {}))

        if tag == "DATA":
            # DATA tags indicate KPIs or similar, which are store in a
            # table called "PREFIX DATA DOMAIN"; for now we just store
            # them and then we report them upon COMPLETION
            domain = attachments['domain']
            assert isinstance (domain, str), \
                "data domain name '%s' is a %s, need a string" \
                % (domain, type(domain).__name__)
            name = attachments['name']
            assert isinstance (domain, str), \
                "data name '%s' is a %s, need a string" \
                % (name, type(name).__name__)
            value = attachments['value']
            doc['data'].setdefault(domain, {})
            if isinstance(value, str):
                # fix bad UTF8
                value = commonl.mkutf8(value)
            # append target name to the column -- otherwise summaries
            # loose that information
            if target and target.fullid not in name:
                name = name + f" ({target.fullid})"
            doc['data'][domain][name] = value
            return

        if message.startswith("COMPLETION"):
            # The *tag* for COMPLETION says what was the final result
            data = {
                # we need to store this in the table; we only want to
                # set the fields we want to increase -- so works
                # table_row_inc()
                "RunID": runid,
                "Total Count": 1,
            }

            result = None
            if tag == "PASS":
                data['Passed'] = 1
                result = "P"
            elif tag == "FAIL":
                data['Failed'] = 1
                result = "F"
            elif tag == "ERRR":
                data['Errored'] = 1
                result = "E"
            elif tag == "BLCK":
                data['Blocked'] = 1
                result = "B"
            elif tag == "SKIP":
                data['Skipped'] = 1
                result = "S"

            # we specify runid here to filter which row we want to
            # update/create, since if it is already existing we want
            # to update the count
            # No need to encode here, all the field names are valid SQL
            try:
                self.table_row_inc("Summary", "RunID", runid, **data)
            except mariadb.Error as e:
                logging.error(f"Summary: {tc_name}:{hashid}: MariaDB error: %s" % e)

            # Record a mapping of runid-testcasename -> hashid; this

            # is needed so we can refer to various things that use the
            # hashid, like for example reports
            # (report-RUNID:HASHID.ANYTHING)
            #
            try:
                self.table_row_update(
                    "HashIDs", "RunID-TestcaseName", runid + "##" + tc_name,
                    **{ 'HashID': hashid })
            except mariadb.Error as e:
                logging.error(f"HashIDs: {tc_name}:{hashid}: MariaDB error: {str(e)}")

            # Update --id-extra KEY=VALUE
            if reporter.runid_extra:
                # Note we might be overriding existing values--in
                # theory we shouldn't because reporters.runid_extra
                # should be always all the same.
                try:
                    self.table_row_update("Summary", "RunID", runid,
                                          **reporter.runid_extra)
                except mariadb.Error as e:
                    logging.error(f"HashIDs: {tc_name}:{hashid}: MariaDB error: {str(e)}")

            # Any field name over 64 chars will make SQL (at least
            # MariaDB) complain sooo..encoding time; we have a table
            # mapping field name by hash to a name

            # Flush the collected KPI data
            # note the test case name is not used here at all, just
            # the domain
            for domain, data in doc['data'].items():
                # convert subdictionaries and lists into columns
                data_flat = {}
                for key, value in commonl.dict_to_flat(data):
                    key = self._id_maybe_encode(key)
                    data_flat[key] = value
                try:
                    self.table_row_update(domain, "RunID", runid,
                                          prefix_bare = "DATA ", **data_flat)
                except mariadb.Error as e:
                    logging.error(f"domain: {tc_name}:{hashid}: MariaDB error: {str(e)}")

            # Add to the table of executed testcases/results
            # We need to index by test case and column by RunID. Why?
            # because if we index by RunId and column by testcase
            # name...SQL complains our test case names get too long
            # (they do) for column IDs; if we encode those so they
            # are short, it complains that we have too many.... welp.
            if result:
                # FIXME: add more info so we can do a link to
                # result
                try:
                    self.table_row_update(
                        "History", "Testcase name", tc_name,
                        **{ self._id_maybe_encode(runid, max_len = 63): result })
                except mariadb.Error as e:
                    logging.error(f"History: {tc_name}:{hashid}: MariaDB error: {str(e)}")
