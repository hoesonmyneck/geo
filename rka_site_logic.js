var txtroom = '';
                var txtbuilding = '';
                var txtgeonim = '';
                var txtground = '';
                var txtats = '';
                var _index = '';

                function search() {
                    txtroom = '';
                    txtbuilding = '';
                    txtgeonim = '';
                    txtground = '';
                    txtats = '';
                    _index = '';
                    count = 0;
                    $('#steps').empty();

                    rca = $("#rca").val();
                    $.ajax({
                        url: '/api/detailed/s_buildings,s_grounds_new,s_pb?source={"query":{"term":{"rca":"' + $('#rca').val() + '"}}}',
                        type: 'GET',
                        dataType: 'json',
                        data: '',
                        //data: '{"query":{"term":{"rca":"1201300000001526"}}}',
                        success: function (data) {
                            $("#accordion1").empty();
                            count = 1;
                            //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_buildings,s_grounds_new,s_pb?source={"query":{"term":{"rca":"' + $('#rca').val() + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle" data-toggle="collapse" data-parent="#accordion1" href="#collapse_1" aria-expanded="false"> Запрос #1 </a> </h4> </div> <div id="collapse_1" class="panel-collapse collapse in" style="" aria-expanded="false"> <div class="panel-body" id="steps1"> </div> </div> </div>')
                            $('#steps1').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_buildings,s_grounds_new,s_pb?source={"query":{"term":{"rca":"' + $('#rca').val() + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            trv(data);
                        }
                    });
                }

                function trv(data) {
                    dd = data;
                    total = data.totalCount;
                    if (total >= 1) {
                        _index = data.data[0]._index;
                        mdata = data.data[0];
                        if (data.actual == 0) {
                            $('#address_line').text("По введенному значению РКА ничего не найдено.");
                            return;
                        }
                        if (_index == "s_grounds_new") {
                            grounds_trv(mdata.id);
                        } else if (_index == "s_buildings") {
                            buildings_trv(mdata.id);
                        } else if (_index == "s_pb") {
                            $.ajax({
                                url: '/api/detailed/d_rooms_types?source={"query":{"term":{"_id":"' + mdata.d_room_type_id + '"}}}',
                                type: 'GET',
                                dataType: 'json',
                                data: '',
                                success: function (data) {
                                    count++;
                                    // $('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_rooms_types?source={"query":{"term":{"_id":"' + mdata.d_room_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                                    $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_rooms_types?source={"query":{"term":{"_id":"' + mdata.d_room_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    txtroom = data.data[0].short_value_ru + mdata.number;
                                    buildings_trv(mdata.s_building_id);
                                }
                            });

                        }
                    } else {
                        $('#address_line').text("По введенному значению РКА ничего не найдено.");
                    }
                }

                function wr() {
                    txt = '';
                    if (txtats != '')
                        txt = txtats;
                    if (txtgeonim != '')
                        txt = txt + ' ' + txtgeonim;
                    if (txtground != '')
                        txt = txt + ' ' + txtground;
                    if (txtbuilding != '')
                        txt = txt + ' ' + txtbuilding;
                    if (txtroom != '')
                        txt = txt + ' ' + txtroom;

                    $('#address_line').html("По данному РКА зарегистрирован следующий адрес объекта недвижимости:<br>"+txt);
                }



                function ats_trv(ats_id) {
                    $.ajax({
                        url: '/api/detailed/s_ats?source={"query":{"term":{"_id":"' + ats_id + '"}}}',
                        type: 'GET',
                        dataType: 'json',
                        data: '',
                        success: function (data) {
                            count++;
                            $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                            //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_ats?source={"query":{"term":{"_id":"' + ats_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_ats?source={"query":{"term":{"_id":"' + ats_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            adata = data;
                            $.ajax({
                                url: '/api/detailed/d_ats_types?source={"query":{"term":{"_id":"' + data.data[0].d_ats_type_id + '"}}}',
                                type: 'GET',
                                dataType: 'json',
                                data: '',
                                success: function (data) {
                                    count++;
                                    $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                                    //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_ats_types?source={"query":{"term":{"_id":"' + adata.data[0].d_ats_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_ats_types?source={"query":{"term":{"_id":"' + adata.data[0].d_ats_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    txtats = data.data[0].value_ru + ' ' + adata.data[0].name_rus + ' ' + txtats;
                                    if (adata.data[0].parent_id != "") {
                                        ats_trv(adata.data[0].parent_id);
                                    } else {
                                        wr();
                                    }
                                }
                            });
                        }
                    });
                }

                function geonim_trv(geonim_id) {
                    $.ajax({
                        url: '/api/detailed/s_geonims?source={"query":{"term":{"_id":"' + geonim_id + '"}}}',
                        type: 'GET',
                        dataType: 'json',
                        data: '',
                        success: function (data) {
                            count++;
                            $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                            //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_geonims?source={"query":{"term":{"_id":"' + geonim_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_geonims?source={"query":{"term":{"_id":"' + geonim_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            gdata = data;
                            $.ajax({
                                url: '/api/detailed/d_geonims_types?source={"query":{"term":{"_id":"' + data.data[0].d_geonims_type_id + '"}}}',
                                type: 'GET',
                                dataType: 'json',
                                data: '',
                                success: function (data) {
                                    count++;
                                    $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                                    //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_geonims_types?source={"query":{"term":{"_id":"' + gdata.data[0].d_geonims_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_geonims_types?source={"query":{"term":{"_id":"' + gdata.data[0].d_geonims_type_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                    txtgeonim = data.data[0].value_ru + ' ' + gdata.data[0].name_rus + ' ' + txtgeonim;
                                    if (gdata.data[0].parent_id != "") {
                                        geonim_trv(gdata.data[0].parent_id);
                                    } else {
                                        ats_trv(gdata.data[0].s_ats_id);
                                    }
                                }
                            });
                        }
                    });
                }

                function buildings_trv(building_id) {
                    console.log(building_id);
                    $.ajax({
                        url: '/api/detailed/s_buildings?source={"query":{"term":{"_id":"' + building_id + '"}}}',
                        type: 'GET',
                        dataType: 'json',
                        data: '',
                        success: function (data) {
                            count++;
                            $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                            //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_buildings?source={"query":{"term":{"_id":"' + building_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_buildings?source={"query":{"term":{"_id":"' + building_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            txtbuilding = data.data[0].number + '/' + txtbuilding;
                            if (data.data[0].parent_id != "") {
                                buildings_trv(data.data[0].parent_id);
                            } else {
                                bdata = data.data[0];
                                $.ajax({
                                    url: '/api/detailed/d_buildings_pointers?source={"query":{"term":{"_id":"' + data.data[0].d_buildings_pointer_id + '"}}}',
                                    type: 'GET',
                                    dataType: 'json',
                                    data: '',
                                    success: function (data) {
                                        count++;
                                        $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                                        //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_buildings_pointers?source={"query":{"term":{"_id":"' + bdata.d_buildings_pointer_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                                        $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/d_buildings_pointers?source={"query":{"term":{"_id":"' + bdata.d_buildings_pointer_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');

                                        txtbuilding = data.data[0].short_value_ru + txtbuilding.slice(0, txtbuilding.length - 1);
                                        if (bdata.s_geonim_id != "") {
                                            geonim_trv(bdata.s_geonim_id);
                                        } else {
                                            ats_trv(bdata.s_ats_id);
                                        }
                                    }
                                });
                            }
                        }
                    });
                }



                function grounds_trv(ground_id) {
                    $.ajax({
                        url: '/api/detailed/s_grounds_new?source={"query":{"term":{"_id":"' + ground_id + '"}}}',
                        type: 'GET',
                        dataType: 'json',
                        data: '',
                        success: function (data) {
                            count++;
                            $('#accordion1').append('<div class="panel panel-default"> <div class="panel-heading"> <h4 class="panel-title"> <a class="accordion-toggle collapsed" data-toggle="collapse" data-parent="#accordion1" href="#collapse_' + count + '"> Запрос #' + count + ' </a> </h4> </div> <div id="collapse_' + count + '" class="panel-collapse collapse" style="height: 0px;"> <div class="panel-body" style="height:200px; overflow-y:auto;" id="steps' + count + '"> </div> </div> </div>');
                            //$('#steps').append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_grounds_new?source={"query":{"term":{"_id":"' + ground_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');
                            $('#steps' + count).append('<div class="well" style="word-wrap: break-word;"><h5>Запрос:</h5><code>' + 'http://data.egov.kz/api/detailed/s_grounds_new?source={"query":{"term":{"_id":"' + ground_id + '"}}}' + '</code><br/><br/><h5>Ответ:</h5><code>' + JSON.stringify(data.data) + '</code></div>');

                            txtground = 'Номер участка ' + data.data[0].number + ' кадастровый номер' + data.data[0].cadastre_number;
                            if (data.data[0].s_geonim_id != "") {
                                geonim_trv(data.data[0].s_geonim_id);
                            } else {
                                ats_trv(data.data[0].s_ats_id);
                            }
                        }
                    });
                }

            