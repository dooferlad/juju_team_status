package server

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/googollee/go-socket.io"
	"github.com/gorilla/mux"
	"gopkg.in/mgo.v2"
	"gopkg.in/mgo.v2/bson"
)

type ServerState struct {
	bugs     *mgo.Collection
	meta     *mgo.Collection
	messages chan string
	socket   *socketio.Socket
}

// collectionToJson sends all data in a Mongo collection as JSON to a client
// by writing it directly into the HTTP response.
func collectionToJson(response http.ResponseWriter, collection *mgo.Collection) {
	var data []bson.M
	query := collection.Find(nil)
	count, err := query.Count()
	if err != nil {
		panic(err)
	}
	response.Header().Set("Content-Type", "application/vnd.api+json")
	if count > 0 {
		// We don't want to encode a zero length list as "null", so we only
		// write a response if there is something to write. This makes JSON
		// decoders happier.
		err = query.All(&data)
		if err != nil {
			panic(err)
		}
		json.NewEncoder(response).Encode(&data)
	}
}

func (state ServerState) apiBugsHandler(response http.ResponseWriter, request *http.Request) {
	collectionToJson(response, state.bugs)
}
func (state ServerState) apiMetaHandler(response http.ResponseWriter, request *http.Request) {
	collectionToJson(response, state.meta)
}
func (state ServerState) apiPing(response http.ResponseWriter, request *http.Request) {
	state.messages <- "ping"
}

// forwardUpdatesToSocketIO sends a ping to the connected clients when we
// receive a ping from a data source that something updated.
func (state *ServerState) forwardUpdatesToSocketIO() {
	server, err := socketio.NewServer(nil)
	if err != nil {
		log.Fatal(err)
	}
	server.On("connection", func(so socketio.Socket) {
		so.Join("updates")
		state.socket = &so

		so.Emit("update", "hello")
		so.On("chat message", func(msg string) {
			log.Println("emit:", so.Emit("chat message", msg))
			so.BroadcastTo("chat", "chat message", msg)
		})
		so.On("disconnection", func() {
		})
	})
	server.On("error", func(so socketio.Socket, err error) {
		log.Println("error:", err)
	})

	http.Handle("/socket.io/", server)

	for {
		_ = <-state.messages
		if state.socket != nil {
			so := *state.socket
			so.Emit("update", "db")
		}
	}
}

var router = mux.NewRouter()

// Run runs the web server
func Run() {
	// We spend most of our time presenting data from external sources, which
	// has already been inserted into a database (MongoDB), over our API as
	// JSON. Dial out to the database and get the collections set up.
	sess, err := mgo.Dial("localhost")
	if err != nil {
		panic(err)
	}
	defer sess.Close()
	state := ServerState{}
	db := sess.DB("juju_team_status")
	state.bugs = db.C("bugs_filtered")
	state.meta = db.C("projects_meta")

	// We tell the web client that we have updated information by communicating
	// with it over socket.io (http://socket.io/)
	state.socket = nil

	// We are told about database updates via web hook, which dumps the messages
	// in this queue for the socket.io handler function to pick up
	state.messages = make(chan string, 1000)

	// Paths on the web server are handled by functions. Wire them up.
	api := router.PathPrefix("/API").Subrouter()
	router.PathPrefix("/").Handler(http.FileServer(http.Dir("./static/")))

	api.HandleFunc("/bugs", state.apiBugsHandler)
	api.HandleFunc("/meta", state.apiMetaHandler)

	// TODO: Attach this to a services API rather than the public one!
	api.HandleFunc("/ping", state.apiPing)

	// Start the real time messaging handler
	go state.forwardUpdatesToSocketIO()

	http.Handle("/", router)

	log.Fatal(http.ListenAndServe(":9873", nil))
}
